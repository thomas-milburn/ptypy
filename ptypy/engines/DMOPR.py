# -*- coding: utf-8 -*-
"""
Difference Map reconstruction engine.
Independent-Probe flavour.

This file is part of the PTYPY package.

    :copyright: Copyright 2014 by the PTYPY team, see AUTHORS.
    :license: GPLv2, see LICENSE for details.
"""
import numpy as np
import time
from .. import utils as u
from ..utils.verbose import logger
from ..utils import parallel
from engine_utils import basic_fourier_update, reduce_dimension
from . import BaseEngine
from ..core import Storage

__all__ = ['DMOPR']

DEFAULT = u.Param(
    alpha=1,                       # Difference map parameter
    probe_update_start=2,          # Number of iterations before probe update starts
    update_object_first=True,      # If True update object before probe
    overlap_converge_factor=0.05,  # Threshold for interruption of the inner overlap loop
    overlap_max_iterations=10,     # Maximum of iterations for the overlap constraint inner loop
    object_inertia=1e-4,           # Weight of the current object in the update, formally DM_smooth_amplitude
    fourier_relax_factor=0.05,     # If rms error of model vs diffraction data is smaller than this fraction,
                                   # Fourier constraint is met
    obj_smooth_std=None,           # Gaussian smoothing (pixel) of the current object prior to update
    clip_object=None,              # None or tuple(min,max) of desired limits of the object modulus,
                                   # currently in under common in documentation
    IP_metric=1.,                  # The metric factor in the exit + probe augmented space.
    subspace_dim=0.,               # The dimension of the subspace spanned by the probe ensemble
)

    
class DMOPR(BaseEngine):
    
    DEFAULT = DEFAULT

    def __init__(self, ptycho_parent, pars=None):
        """
        Difference map reconstruction engine with Orthogonal probe relaxation
        """
        if pars is None:
            pars = DEFAULT.copy()
            
        super(DMOPR, self).__init__(ptycho_parent, pars)

    def engine_initialize(self):
        """
        Prepare for reconstruction.
        """
        self.error = []

        # Generate container copies
        self.ob_buf = self.ob.copy(self.ob.ID + '_alt', fill=0.)
        self.ob_nrm = self.ob.copy(self.ob.ID + '_nrm', fill=0.)
        self.ob_viewcover = self.ob.copy(self.ob.ID + '_vcover', fill=0.)

        prviewdata = {}
        for vID, v in self.pr.views.iteritems():
            # Get the associated diffraction frame
            di_view = v.pod.di_view
            # Reformat the layer
            v.layer = (di_view.layer, v.layer)
            # Deactivate if the associate di_view is inactive (to spread the probe across nodes consistently with diff)
            v.active = di_view.active
            # Store the current view data so we can restore it after reformat
            if v.active:
                prviewdata[vID] = v.data.copy()

        # Let all probe storages reshape themselves
        self.pr.reformat()

        # Store probe data back
        for vID, v in self.pr.views.iteritems():
            if v.active:
                self.pr[v] = prviewdata[vID]
        del prviewdata

        # Create array to store OPR modes
        dim = self.p.subspace_dim if self.p.subspace_dim > 0 else 1
        self.OPR_modes = {}
        self.OPR_coeffs = {}
        self.local_layers = {}
        self.local_indices = {}
        for sID, s in self.pr.S.iteritems():
            shape = (dim,) + s.data.shape[1:]
            dtype = s.data.dtype
            self.OPR_modes[sID] = np.zeros(shape=shape, dtype=dtype)

            # Prepare a sorted list (with index) of all layers (which are locally available through views)
            unique_layers = sorted(set([v.layer for v in s.owner.views_in_storage(s=s, active=False)]))
            layers = list(enumerate(unique_layers))

            # Then make a list of layers held locally by the node
            self.local_layers[sID] = [x for x in layers if x[1] in s.layermap]
            self.local_indices[sID] = [i for i, l in self.local_layers[sID]]

        # Create a copy of probe Container for DM iterations
        self.pr_old = self.pr.copy(self.pr.ID + '_old')

    def engine_prepare(self):
        """
        Last minute initialization. Everything that needs to be recalculated when new data arrives.
        """

        self.pbound = {}
        for name, s in self.di.S.iteritems():
            self.pbound[name] = .25 * self.p.fourier_relax_factor**2 * s.pbound_stub
        
        # Fill object with coverage of views
        for name, s in self.ob_viewcover.S.iteritems():
            s.fill(s.get_view_coverage())

    def engine_iterate(self, num=1):
        """
        Compute `num` iterations.
        """
        to = 0.
        tf = 0.
        for it in range(num):
            t1 = time.time() 
            
            # Fourier update  
            error_dct = self.fourier_update()

            # Probe consistency update
            self.probe_consistency_update()

            t2 = time.time()
            tf += t2 - t1
            
            # Overlap update
            self.overlap_update()

            t3 = time.time()
            to += t3 - t2

            # Maintain scales
            #self.rescale_obj()

        logger.info('Time spent in Fourier update: %.2f' % tf)
        logger.info('Time spent in Overlap update: %.2f' % to)
        error = parallel.gather_dict(error_dct)
        return error

    def engine_finalize(self):
        """
        Try deleting ever helper container.
        """

        # already fixed elsewhere by storing OPR coeffs and modes in runtime
        # commented out on 20170411
        #if parallel.master:
        #    from .. import io
        #    io.h5write('Dump.h5', modes=self.OPR_modes.values()[0])

        containers = [
            self.ob_buf,
            self.ob_nrm,
            self.ob_viewcover,
            self.pr_old]

        for c in containers:
            logger.debug('Attempt to remove container %s' % c.ID)
            del self.ptycho.containers[c.ID]
        #    IDM.used.remove(c.ID)
        
        del self.ob_buf
        del self.ob_nrm 
        del self.ob_viewcover 
        del self.pr_old

        del containers

    #def rescale_obj(self):
    #    """
    #    Rescale object and probes.
    #    :return:
    #    """

    def fourier_update(self):
        """
        DM Fourier constraint update (including DM step)
        """
        error_dct = {}
        for name, di_view in self.di.V.iteritems():
            if not di_view.active:
                continue
            pbound = self.pbound[di_view.storage.ID]
            error_dct[name] = basic_fourier_update(di_view, pbound=pbound, alpha=self.p.alpha)
        return error_dct

    def overlap_update(self):
        """
        DM overlap constraint update.
        """
        # Condition to update probe
        do_update_probe = (self.p.probe_update_start <= self.curiter)
         
        for inner in range(self.p.overlap_max_iterations):
            pre_str = 'Iteration (Overlap) #%02d:  ' % inner
            
            # Update object first
            if self.p.update_object_first or (inner > 0):
                # Update object
                logger.debug(pre_str + '----- object update -----')
                self.object_update()
                               
            # Exit if probe should not be updated yet
            if not do_update_probe:
                break
            
            # Update probe
            logger.debug(pre_str + '----- probe update -----')
            change = self.probe_update()
            logger.debug(pre_str + 'change in probe is %.3f' % change)

            # Stop iteration if probe change is small
            if change < self.p.overlap_converge_factor:
                break

    def object_update(self):
        """
        DM object update.
        """
        ob = self.ob
        ob_nrm = self.ob_nrm
        
        # Fill container
        if not parallel.master:
            ob.fill(0.0)
            ob_nrm.fill(0.)
        else:
            for name, s in self.ob.S.iteritems():
                # in original code:
                # DM_smooth_amplitude = (p.DM_smooth_amplitude * max_power * p.num_probes * Ndata) / np.prod(asize)
                # using the number of views here, but don't know if that is good.
                # cfact = self.p.object_inertia * len(s.views)
                cfact = self.p.object_inertia  # * (self.ob_viewcover.S[name].data + 1.)
                
                if self.p.obj_smooth_std is not None:
                    logger.info('Smoothing object, average cfact is %.2f + %.2fj' %
                                (np.mean(cfact).real, np.mean(cfact).imag))
                    smooth_mfs = [0, self.p.obj_smooth_std, self.p.obj_smooth_std]
                    s.data[:] = cfact * u.c_gf(s.data, smooth_mfs)
                else:
                    s.data[:] = s.data * cfact
                    
                ob_nrm.S[name].fill(cfact)
        
        # DM update per node
        for name, pod in self.pods.iteritems():
            if not pod.active:
                continue
            pod.object += pod.probe.conj() * pod.exit * pod.object_weight
            ob_nrm[pod.ob_view] += u.cabs2(pod.probe) * pod.object_weight
        
        # Distribute result with MPI
        for name, s in self.ob.S.iteritems():
            # Get the np arrays
            nrm = ob_nrm.S[name].data
            parallel.allreduce(s.data)
            parallel.allreduce(nrm)
            s.data /= nrm
                
            # Clip object
            if self.p.clip_object is not None:
                clip_min, clip_max = self.p.clip_object
                ampl_obj = np.abs(s.data)
                phase_obj = np.exp(1j * np.angle(s.data))
                too_high = (ampl_obj > clip_max)
                too_low = (ampl_obj < clip_min)
                s.data[too_high] = clip_max * phase_obj[too_high]
                s.data[too_low] = clip_min * phase_obj[too_low]
                
    def probe_update(self):
        """
        DM probe update - independent probe version
        """
        pr = self.pr

        # DM update
        for name, pod in self.pods.iteritems():
            if not pod.active:
                continue
            pod.probe = self.p.IP_metric * self.pr_old[pod.pr_view] + pod.object.conj() * pod.exit
            pod.probe /= u.cabs2(pod.object) + self.p.IP_metric

            # Apply probe support if requested
            support = self.probe_support.get(name)
            if support is not None: 
                pod.probe *= self.probe_support[name]

        change = u.norm2(pr.S.values()[0].data - self.pr_old.S.values()[0].data)
        change = parallel.allreduce(change)

        return np.sqrt(change / pr.S.values()[0].nlayers)

    def probe_consistency_update(self):
        """
        DM probe consistency update for orthogonal probe relaxation.

        Here what we do is compute a singular value decomposition on the ensemble of probes.
        Because this is difference map, SVD is actually on (1+alpha)*pr - pr_old, and not on pr.
        """

        if self.p.subspace_dim == 0:
            # Boring case equivalent to normal DM - do not implement
            raise NotImplementedError('0 dim case is not implemented.')

        for sID, prS in self.pr.S.iteritems():
            pr_oldS = self.pr_old.S[sID]
            # pr_input = np.array([2 * self.pr[v] - self.pr_old[v] for v in self.pr.views.values() if v.active])
            pr_input = np.array([2 * prS[l] - pr_oldS[l] for i, l in self.local_layers[sID]])

            new_pr, modes, coeffs = reduce_dimension(a=pr_input, dim=self.p.subspace_dim, local_indices=self.local_indices[sID])

            self.OPR_modes[sID] = modes
            self.OPR_coeffs[sID] = coeffs

            ### Storing OPR modes and coeffs in dumps
            if self.ptycho.p.io.autosave is not None and self.ptycho.p.io.autosave.interval > 1:
                if self.curiter % self.ptycho.p.io.autosave.interval == 0:
                    if self.ptycho.p.io.autosave.store_OPR_iter:
                        self.ptycho.runtime['OPR_modes'] = self.OPR_modes
                        self.ptycho.runtime['OPR_coeffs'] = self.OPR_coeffs

            # Update probes
            for k, il in enumerate(self.local_layers[sID]):
                pr_oldS[il[1]] += new_pr[k] - prS[il[1]]

        return



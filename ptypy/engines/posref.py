# -*- coding: utf-8 -*-
"""
Position refinement module.

This file is part of the PTYPY package.

    :copyright: Copyright 2014 by the PTYPY team, see AUTHORS.
    :license: GPLv2, see LICENSE for details.
"""
from ..core import View
from .. import utils as u
from ..utils.verbose import log
import numpy as np


class PositionRefine(object):
    def __init__(self, p):
        self.p = p

    def update_view_position(self, di_view):
        '''
        Refines the position of a single diffraction view
        Parameters
        ----------
        di_view : ptypy.core.classes.View
            A diffraction view that we wish to refine.

        Returns
        -------
        numpy.ndarray
            A length 2 numpy array with the position increments for x and y co-ordinates respectively
        '''

        raise NotImplementedError('This method needs to be overridden in order to position correct')

    def update_constraints(self, iteration):
        '''

        Parameters
        ----------
        iteration : int
            The current iteration of the engine.
        '''

        raise NotImplementedError('This method needs to be overridden in order to position correct')

    def cleanup(self):
        '''
        Cleans up every iteration
        '''


class AnnealingRefine(PositionRefine):

    def __init__(self, position_refinement_parameters, Cobj):
        '''
        Annealing Position Refinement.
        Refines the positions by the following algorithm:

        A.M. Maiden, M.J. Humphry, M.C. Sarahan, B. Kraus, J.M. Rodenburg,
        An annealing algorithm to correct positioning errors in ptychography,
        Ultramicroscopy, Volume 120, 2012, Pages 64-72
        Parameters
        ----------
        position_refinement_parameters : ptypy.utils.parameters.Param
            The parameter tree for the refinement

        Cobj : ptypy.core.classes.Container
            The current pbject container object

        '''
        super(AnnealingRefine, self).__init__(position_refinement_parameters)

        self.Cobj = Cobj  # take a reference here. It would be cool if we could make this read-only or something

        # Updated before each iteration by self.update_constraints
        self.max_shift_dist = None


    def fourier_error(self, di_view, obj, metric="fourier"):
        '''
        Parameters
        ----------
        di_view : ptypy.core.classes.View
            A diffraction view for which we wish to calculate the error.

        obj : numpy.ndarray
            The current calculated object for which we wish to evaluate the error against.
        metric : str
            "fourier" or "photon"
        Returns
        -------
        np.float
            The calculated fourier error
        '''
        af2 = np.zeros_like(di_view.data)
        for name, pod in di_view.pods.iteritems():
            af2 += u.abs2(pod.fw(pod.probe*obj))
        if metric == "fourier":
            return np.sum(di_view.pod.mask * (np.sqrt(af2) - np.sqrt(np.abs(di_view.data)))**2)
        elif metric == "photon":
            return (np.sum(di_view.pod.mask * (af2 - di_view.data)**2 / (di_view.data + 1.)) / np.prod(af2.shape))
        else:
            raise NotImplementedError("Metric %s is currently not implemented" %metric)


    def update_view_position(self, di_view, metric):
        '''
        Refines the positions by the following algorithm:

        A.M. Maiden, M.J. Humphry, M.C. Sarahan, B. Kraus, J.M. Rodenburg,
        An annealing algorithm to correct positioning errors in ptychography,
        Ultramicroscopy, Volume 120, 2012, Pages 64-72

        Algorithm Description:
        Calculates random shifts around the original position and calculates the fourier error. If the fourier error
        decreased the randomly calculated postion will be used as new position.

        Parameters
        ----------
        di_view : ptypy.core.classes.View
            A diffraction view that we wish to refine.

        Returns
        -------
        numpy.ndarray
            A length 2 numpy array with the position increments for x and y co-ordinates respectively
        '''        
        # there might be more than one object view
        ob_view = di_view.pod.ob_view

        initial_coord = ob_view.coord.copy()
        coord = initial_coord
        psize = ob_view.psize.copy()

        # if you cannot move far, do nothing
        if np.max(psize) >= self.max_shift_dist:
            return np.zeros((2,))
            
        # This can be optimized by saving existing iteration fourier error...
        error = self.fourier_error(di_view, ob_view.data, metric)
        
        for i in range(self.p.nshifts):
            # Generate coordinate shift in one of the 4 cartesian quadrants
            a, b = np.random.uniform(np.max(psize), self.max_shift_dist, 2)
            delta = np.array([(-1)**i * a, (-1)**(i//2) *b])

            if np.linalg.norm(delta) > self.p.max_shift:
                # Positions drifted too far, skip this position
                continue

            # Move view to new position
            new_coord = initial_coord + delta 
            ob_view.coord = new_coord
            ob_view.storage.update_views(ob_view)
            data = ob_view.data
            
            # catch bad slicing
            if not np.allclose(data.shape, ob_view.shape):
                continue 
                
            new_error = self.fourier_error(di_view, data, metric)
            
            if new_error < error:
                # keep
                error = new_error
                coord = new_coord
                log(4, "Position correction: %s, coord: %s" % (di_view.ID, coord))
                
        ob_view.coord = coord
        ob_view.storage.update_views(ob_view)        
        return coord - initial_coord

    def update_constraints(self, iteration):
        '''

        Parameters
        ----------
        iteration : int
            The current iteration of the engine.
        '''

        start, end = self.p.start, self.p.stop

        # Compute the maximum shift allowed at this iteration
        self.max_shift_dist = self.p.amplitude * (end - iteration) / (end - start)

    @property
    def citation_dictionary(self):
        return {
            "title" : 'An annealing algorithm to correct positioning errors in ptychography',
            "author" : 'Maiden et al.',
            "journal" : 'Ultramicroscopy',
            "volume" : 120,
            "year" : 2012,
            "page" : 64,
            "doi" : '10.1016/j.ultramic.2012.06.001',
            "comment" : 'Position Refinement using annealing algorithm'}

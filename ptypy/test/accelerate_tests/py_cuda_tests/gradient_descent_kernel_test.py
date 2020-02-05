'''


'''

import unittest
import numpy as np


def have_pycuda():
    try:
        import pycuda.driver
        return True
    except:
        return False


if have_pycuda():
    import pycuda.driver as cuda
    from pycuda import gpuarray
    from pycuda.tools import make_default_context
    from ptypy.accelerate.py_cuda.kernels import GradientDescentKernel


COMPLEX_TYPE = np.complex64
FLOAT_TYPE = np.float32
INT_TYPE = np.int32


@unittest.skipIf(not have_pycuda(), "no PyCUDA or GPU drivers available")
class GradientDescentKernelTest(unittest.TestCase):

    def setUp(self):
        import sys
        np.set_printoptions(threshold=sys.maxsize, linewidth=np.inf)
        cuda.init()
        self.ctx = make_default_context()
        self.stream = cuda.Stream()

    def tearDown(self):
        np.set_printoptions()
        self.ctx.pop()
        self.ctx.detach()

    def prepare_arrays(self, performance=False):
        if not performance:
            nmodes = 2
            N_buf = 4 
            N = 3 
            A = 3 
        else:
            nmodes = 4
            N_buf = 8
            N = 32
            A =  1024
        i_sh = (N, A, A)
        e_sh = (N*nmodes, A, A)
        f_sh = (N_buf, A, A)
        a_sh = (N_buf * nmodes, A, A)
        w = np.ones(i_sh, dtype=FLOAT_TYPE)
        for idx, sl in enumerate(w):
            sl[idx % A, idx % A] = 0.0
        X, Y, Z = np.indices(a_sh, dtype=COMPLEX_TYPE)
        b_f = X + 1j * Y
        b_a = Y + 1j * Z
        b_b = Z + 1j * X
        err_sum = np.zeros((N,), dtype=FLOAT_TYPE)
        addr = np.zeros((N, nmodes, 5, 3), dtype=INT_TYPE)
        I = np.empty(i_sh, dtype=FLOAT_TYPE)
        I[:] = np.round(np.abs(b_f[:N])**2 % 20)
        for pos_idx in range(N):
            for mode_idx in range(nmodes):
                exit_idx = pos_idx * nmodes + mode_idx
                addr[pos_idx, mode_idx] = np.array([[mode_idx, 0, 0],
                                                    [0, 0, 0],
                                                    [exit_idx, 0, 0],
                                                    [pos_idx, 0, 0],
                                                    [pos_idx, 0, 0]], dtype=INT_TYPE)
        return (gpuarray.to_gpu(b_f),
                gpuarray.to_gpu(b_a),
                gpuarray.to_gpu(b_b),
                gpuarray.to_gpu(I),
                gpuarray.to_gpu(w),
                gpuarray.to_gpu(err_sum),
                gpuarray.to_gpu(addr))

    def test_make_model(self):
        b_f, b_a, b_b, I, w, err_sum, addr = self.prepare_arrays()

        GDK = GradientDescentKernel(b_f, addr.shape[1])
        GDK.allocate()
        GDK.make_model(b_f)

        exp_Imodel = np.array([[[1.,  1.,  1.],
                                [3.,  3.,  3.],
                                [9.,  9.,  9.]],

                               [[13., 13., 13.],
                                [15., 15., 15.],
                                [21., 21., 21.]],

                               [[41., 41., 41.],
                                [43., 43., 43.],
                                [49., 49., 49.]],

                               [[85., 85., 85.],
                                [87., 87., 87.],
                                [93., 93., 93.]]], dtype=FLOAT_TYPE)

        np.testing.assert_array_almost_equal(
            exp_Imodel, GDK.gpu.Imodel.get(),
            err_msg="`Imodel` buffer has not been updated as expected")

    @unittest.skip('performance test')
    def test_make_model_performance(self):
        b_f, b_a, b_b, I, w, err_sum, addr = self.prepare_arrays(performance=True)

        GDK = GradientDescentKernel(b_f, addr.shape[1])
        GDK.allocate()
        GDK.make_model(b_f)

    def test_make_a012(self):
        b_f, b_a, b_b, I, w, err_sum, addr = self.prepare_arrays()

        GDK = GradientDescentKernel(b_f, addr.shape[1])
        GDK.allocate()
        GDK.make_a012(b_f, b_a, b_b, I)

        exp_A0 = np.array([[[1.,  1.,  1.],
                            [2.,  2.,  2.],
                            [5.,  5.,  5.]],

                           [[12., 12., 12.],
                            [13., 13., 13.],
                            [16., 16., 16.]],

                           [[37., 37., 37.],
                            [38., 38., 38.],
                            [41., 41., 41.]],

                           [[0.,  0.,  0.],
                            [0.,  0.,  0.],
                            [0.,  0.,  0.]]], dtype=FLOAT_TYPE)
        np.testing.assert_array_almost_equal(
            exp_A0, GDK.gpu.Imodel.get(),
            err_msg="`Imodel` buffer (=A0) has not been updated as expected")

        exp_A1 = np.array([[[0.,  0.,  0.],
                            [1.,  5.,  9.],
                            [0.,  8., 16.]],

                           [[-1., -1., -1.],
                            [8., 12., 16.],
                            [15., 23., 31.]],

                           [[-4., -4., -4.],
                            [13., 17., 21.],
                            [28., 36., 44.]],

                           [[0.,  0.,  0.],
                            [0.,  0.,  0.],
                            [0.,  0.,  0.]]], dtype=FLOAT_TYPE)
        np.testing.assert_array_almost_equal(
            exp_A1, GDK.gpu.LLerr.get(),
            err_msg="`LLerr` buffer (=A1) has not been updated as expected")

        exp_A2 = np.array([[[0.,  4., 12.],
                            [3.,  7., 15.],
                            [8., 12., 20.]],

                           [[-1., 11., 27.],
                            [10., 22., 38.],
                            [23., 35., 51.]],

                           [[-4., 16., 40.],
                            [15., 35., 59.],
                            [36., 56., 80.]],

                           [[0.,  0.,  0.],
                            [0.,  0.,  0.],
                            [0.,  0.,  0.]]], dtype=FLOAT_TYPE)
        np.testing.assert_array_almost_equal(
            exp_A2, GDK.gpu.LLden.get(),
            err_msg="`LLden` buffer (=A2) has not been updated as expected")

    @unittest.skip('performance test')
    def test_make_a012_performance(self):
        b_f, b_a, b_b, I, w, err_sum, addr = self.prepare_arrays(performance=True)

        GDK = GradientDescentKernel(b_f, addr.shape[1])
        GDK.allocate()
        GDK.make_a012(b_f, b_a, b_b, I)

    def test_fill_b(self):
        b_f, b_a, b_b, I, w, err_sum, addr = self.prepare_arrays()
        Brenorm = 0.35
        B = np.zeros((3,), dtype=FLOAT_TYPE)
        B_dev = gpuarray.to_gpu(B)
        GDK = GradientDescentKernel(b_f, addr.shape[1])
        GDK.allocate()
        GDK.make_a012(b_f, b_a, b_b, I)
        GDK.fill_b(Brenorm, w, B_dev)
        B[:] = B_dev.get()

        exp_B = np.array([4699.8,  3953.6, 10963.4], dtype=FLOAT_TYPE)
        np.testing.assert_allclose(
            B, exp_B,
            rtol=1e-7,
            err_msg="`B` has not been updated as expected")

    def test_error_reduce(self):
        b_f, b_a, b_b, I, w, err_sum, addr = self.prepare_arrays()
        GDK = GradientDescentKernel(b_f, addr.shape[1])
        GDK.allocate()
        GDK.npy.LLerr = np.indices(GDK.gpu.LLerr.shape, dtype=FLOAT_TYPE)[0]
        GDK.gpu.LLerr = gpuarray.to_gpu(GDK.npy.LLerr)
        GDK.error_reduce(err_sum)

        exp_err = np.array([0.,  9., 18.], dtype=FLOAT_TYPE)
        np.testing.assert_array_almost_equal(
            exp_err, err_sum.get(),
            err_msg="`err_sum` has not been updated as expected")
        return

    def test_main(self):
        b_f, b_a, b_b, I, w, err_sum, addr = self.prepare_arrays()
        GDK = GradientDescentKernel(b_f, addr.shape[1])
        GDK.allocate()
        GDK.main(b_f, w, I)

        exp_b_f = np.array([[[0. + 0.j,   0. + 0.j,   0. + 0.j],
                             [-0. - 1.j,  -0. - 1.j,  -0. - 1.j],
                             [-0. - 8.j,  -0. - 8.j,  -0. - 8.j]],

                            [[0. + 0.j,   0. + 0.j,   0. + 0.j],
                             [-1. - 1.j,  -1. - 1.j,  -1. - 1.j],
                             [-4. - 8.j,  -4. - 8.j,  -4. - 8.j]],

                            [[-2. + 0.j,  -2. + 0.j,  -2. + 0.j],
                             [-4. - 2.j,  -0. + 0.j,  -4. - 2.j],
                             [-10.-10.j, -10.-10.j, -10.-10.j]],

                            [[-3. + 0.j,  -3. + 0.j,  -3. + 0.j],
                             [-6. - 2.j,  -0. + 0.j,  -6. - 2.j],
                             [-15.-10.j, -15.-10.j, -15.-10.j]],

                            [[-16. + 0.j, -16. + 0.j, -16. + 0.j],
                             [-20. - 5.j, -20. - 5.j, -20. - 5.j],
                             [-32.-16.j, -32.-16.j,  -0. + 0.j]],

                            [[-20. + 0.j, -20. + 0.j, -20. + 0.j],
                             [-25. - 5.j, -25. - 5.j, -25. - 5.j],
                             [-40.-16.j, -40.-16.j,  -0. + 0.j]],

                            [[6. + 0.j,   6. + 0.j,   6. + 0.j],
                             [6. + 1.j,   6. + 1.j,   6. + 1.j],
                             [6. + 2.j,   6. + 2.j,   6. + 2.j]],

                            [[7. + 0.j,   7. + 0.j,   7. + 0.j],
                             [7. + 1.j,   7. + 1.j,   7. + 1.j],
                             [7. + 2.j,   7. + 2.j,   7. + 2.j]]], dtype=COMPLEX_TYPE)
        np.testing.assert_array_almost_equal(
            exp_b_f, b_f.get(),
            err_msg="Auxiliary has not been updated as expected")

        exp_LL = np.array([[[0.,  0.,  0.],
                            [1.,  1.,  1.],
                            [16., 16., 16.]],

                           [[1.,  1.,  1.],
                            [4.,  0.,  4.],
                            [25., 25., 25.]],

                           [[16., 16., 16.],
                            [25., 25., 25.],
                            [64., 64.,  0.]],

                           [[0.,  0.,  0.],
                            [0.,  0.,  0.],
                            [0.,  0.,  0.]]], dtype=FLOAT_TYPE)
        np.testing.assert_array_almost_equal(
            exp_LL, GDK.gpu.LLerr.get(),
            err_msg="LogLikelihood error has not been updated as expected")
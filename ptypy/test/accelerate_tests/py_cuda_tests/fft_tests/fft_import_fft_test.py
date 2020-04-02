
import unittest
from ptypy.test.accelerate_tests.py_cuda_tests import PyCudaTest, have_pycuda
from ptypy.accelerate.py_cuda import import_fft
import os, shutil
from pycuda.tools import make_default_context
from distutils import sysconfig

if have_pycuda():
    import pycuda.driver as cuda
    from pycuda import gpuarray

class ImportFFTTest(PyCudaTest):

    def test_import_fft(self):
        import_fft.import_fft(32, 32)
    
    
    def test_import_fft_different_shape(self):
        import_fft.import_fft(128, 128)
    
    def test_import_fft_same_module_again(self):
        import_fft.import_fft(32, 32)


if __name__=="__main__":
    unittest.main()
# Prints accumulated Caffe2 configuration summary
function (caffe2_print_configuration_summary)
  message(STATUS "")
  message(STATUS "******** Summary ********")
  message(STATUS "General:")
  message(STATUS "  CMake version         : ${CMAKE_VERSION}")
  message(STATUS "  CMake command         : ${CMAKE_COMMAND}")
  message(STATUS "  Git version           : ${CAFFE2_GIT_VERSION}")
  message(STATUS "  System                : ${CMAKE_SYSTEM_NAME}")
  message(STATUS "  C++ compiler          : ${CMAKE_CXX_COMPILER}")
  message(STATUS "  C++ compiler version  : ${CMAKE_CXX_COMPILER_VERSION}")
  message(STATUS "  BLAS                  : ${BLAS}")
  message(STATUS "  CXX flags             : ${CMAKE_CXX_FLAGS}")
  message(STATUS "  Build type            : ${CMAKE_BUILD_TYPE}")
  get_directory_property(tmp DIRECTORY ${PROJECT_SOURCE_DIR} COMPILE_DEFINITIONS)
  message(STATUS "  Compile definitions   : ${tmp}")
  message(STATUS "")

  message(STATUS "  BUILD_BINARY          : ${BUILD_BINARY}")
  message(STATUS "  BUILD_CUSTOM_PROTOBUF : ${BUILD_CUSTOM_PROTOBUF}")
  if (${CAFFE2_LINK_LOCAL_PROTOBUF})
    message(STATUS "    Link local protobuf : ${CAFFE2_LINK_LOCAL_PROTOBUF}")
  else()
    message(STATUS "    Protobuf compiler   : ${PROTOBUF_PROTOC_EXECUTABLE}")
    message(STATUS "    Protobuf includes   : ${PROTOBUF_INCLUDE_DIRS}")
    message(STATUS "    Protobuf libraries  : ${PROTOBUF_LIBRARIES}")
  endif()
  message(STATUS "  BUILD_DOCS            : ${BUILD_DOCS}")
  message(STATUS "  BUILD_PYTHON          : ${BUILD_PYTHON}")
  if (${BUILD_PYTHON})
    message(STATUS "    Python version      : ${PYTHONLIBS_VERSION_STRING}")
    message(STATUS "    Python includes     : ${PYTHON_INCLUDE_DIRS}")
  endif()
  message(STATUS "  BUILD_SHARED_LIBS     : ${BUILD_SHARED_LIBS}")
  message(STATUS "  BUILD_TEST            : ${BUILD_TEST}")

  message(STATUS "  USE_ATEN              : ${USE_ATEN}")
  message(STATUS "  USE_ASAN              : ${USE_ASAN}")
  message(STATUS "  USE_CUDA              : ${USE_CUDA}")
  if(${USE_CUDA})
    message(STATUS "    CUDA version        : ${CUDA_VERSION}")
    message(STATUS "    CuDNN version       : ${CUDNN_VERSION}")
    message(STATUS "    CUDA root directory : ${CUDA_TOOLKIT_ROOT_DIR}")
    message(STATUS "    CUDA library        : ${CUDA_CUDA_LIB}")
    message(STATUS "    CUDA NVRTC library  : ${CUDA_NVRTC_LIB}")
    message(STATUS "    CUDA runtime library: ${CUDA_CUDART_LIBRARY}")
    message(STATUS "    CUDA include path   : ${CUDA_INCLUDE_DIRS}")
    message(STATUS "    NVCC executable     : ${CUDA_NVCC_EXECUTABLE}")
    message(STATUS "    CUDA host compiler  : ${CUDA_HOST_COMPILER}")
  endif()
  message(STATUS "  USE_EIGEN_FOR_BLAS    : ${CAFFE2_USE_EIGEN_FOR_BLAS}")
  message(STATUS "  USE_FFMPEG            : ${USE_FFMPEG}")
  message(STATUS "  USE_GFLAGS            : ${USE_GFLAGS}")
  message(STATUS "  USE_GLOG              : ${USE_GLOG}")
  message(STATUS "  USE_GLOO              : ${USE_GLOO}")
  message(STATUS "  USE_LEVELDB           : ${USE_LEVELDB}")
  if(${USE_LEVELDB})
    message(STATUS "    LevelDB version     : ${LEVELDB_VERSION}")
    message(STATUS "    Snappy version      : ${Snappy_VERSION}")
  endif()
  message(STATUS "  USE_LITE_PROTO        : ${USE_LITE_PROTO}")
  message(STATUS "  USE_LMDB              : ${USE_LMDB}")
  if(${USE_LMDB})
    message(STATUS "    LMDB version        : ${LMDB_VERSION}")
  endif()
  message(STATUS "  USE_METAL             : ${USE_METAL}")
  message(STATUS "  USE_MKL               : ${CAFFE2_USE_MKL}")
  message(STATUS "  USE_MOBILE_OPENGL     : ${USE_MOBILE_OPENGL}")
  message(STATUS "  USE_MPI               : ${USE_MPI}")
  message(STATUS "  USE_NCCL              : ${USE_NCCL}")
  message(STATUS "  USE_NERVANA_GPU       : ${USE_NERVANA_GPU}")
  if(${USE_NERVANA_GPU})
    message(STATUS "    NERVANA_GPU version : ${NERVANA_GPU_VERSION}")
  endif()
  message(STATUS "  USE_NNPACK            : ${USE_NNPACK}")
  message(STATUS "  USE_OBSERVERS         : ${USE_OBSERVERS}")
  message(STATUS "  USE_OPENCV            : ${USE_OPENCV}")
  if(${USE_OPENCV})
    message(STATUS "    OpenCV version      : ${OpenCV_VERSION}")
  endif()
  message(STATUS "  USE_OPENMP            : ${USE_OPENMP}")
  message(STATUS "  USE_PROF              : ${USE_PROF}")
  message(STATUS "  USE_REDIS             : ${USE_REDIS}")
  message(STATUS "  USE_ROCKSDB           : ${USE_ROCKSDB}")
  message(STATUS "  USE_ZMQ               : ${USE_ZMQ}")
endfunction()

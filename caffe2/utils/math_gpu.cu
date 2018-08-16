// Implements the math functions for CPU.

#include "caffe2/utils/math.h"

#include <cub/block/block_reduce.cuh>
#include <cub/cub.cuh>

#include "caffe2/core/context_gpu.h"
#include "caffe2/utils/conversions.h"

#if THRUST_VERSION >= 100800
#define THRUST_SUPPORTS_PER_THREAD
#endif  // THRUST_VERSION >= 100800

namespace caffe2 {
namespace math {

#define DELEGATE_SIMPLE_CUDA_UNARY_FUNCTION(T, Funcname, function)             \
__global__                                                                     \
void _Kernel_##T##_##Funcname(const int N, const T* x, T* y) {                 \
  CUDA_1D_KERNEL_LOOP(i, N) {                                                  \
    y[i] = function(x[i]);                                                     \
  }                                                                            \
}                                                                              \
template <>                                                                    \
void Funcname<T, CUDAContext>(                                                 \
    const int N, const T* x, T* y,                                             \
    CUDAContext* context) {                                                    \
  _Kernel_##T##_##Funcname<<<CAFFE_GET_BLOCKS(N), CAFFE_CUDA_NUM_THREADS,      \
                                 0, context->cuda_stream()>>>(                 \
      N, x, y);                                                                \
}

DELEGATE_SIMPLE_CUDA_UNARY_FUNCTION(float, Exp, expf);
DELEGATE_SIMPLE_CUDA_UNARY_FUNCTION(float, Log, logf);
DELEGATE_SIMPLE_CUDA_UNARY_FUNCTION(float, Cos, cosf);
DELEGATE_SIMPLE_CUDA_UNARY_FUNCTION(float, Sin, sinf);
DELEGATE_SIMPLE_CUDA_UNARY_FUNCTION(float, Abs, fabsf);
DELEGATE_SIMPLE_CUDA_UNARY_FUNCTION(float, Sqrt, sqrtf);
DELEGATE_SIMPLE_CUDA_UNARY_FUNCTION(float, InvSqrt, rsqrtf);

__device__ float cuda_sqrf(const float x) { return x * x; }

DELEGATE_SIMPLE_CUDA_UNARY_FUNCTION(float, Sqr, cuda_sqrf);

#undef DELEGATE_SIMPLE_CUDA_UNARY_FUNCTION

#define DELEGATE_SINCOS_CUDA_FUNCTION(T)                             \
  __global__ void _Kernel_##T##_##SinCos(                            \
      const int N, const T* x, T* ys, T* yc) {                       \
    CUDA_1D_KERNEL_LOOP(i, N) {                                      \
      sincos(x[i], ys + i, yc + i);                                  \
    }                                                                \
  }                                                                  \
  template <>                                                        \
  void SinCos<T, CUDAContext>(                                       \
      const int N, const T* x, T* ys, T* yc, CUDAContext* context) { \
    _Kernel_##T##_##SinCos<<<                                        \
        CAFFE_GET_BLOCKS(N),                                         \
        CAFFE_CUDA_NUM_THREADS,                                      \
        0,                                                           \
        context->cuda_stream()>>>(N, x, ys, yc);                     \
  }

DELEGATE_SINCOS_CUDA_FUNCTION(float)
DELEGATE_SINCOS_CUDA_FUNCTION(double)

#undef DELEGATE_SINCOS_CUDA_FUNCTION

#define DELEGATE_SIMPLE_CUDA_BINARY_INFIX_FUNCTION(T, Funcname, expr)         \
  __global__ void _Kernel_##T##_##Funcname(                                   \
      const int N, const T* a, const T* b, T* y) {                            \
    CUDA_1D_KERNEL_LOOP(i, N) {                                               \
      float r = convert::To<T, float>(a[i]) expr convert::To<T, float>(b[i]); \
      y[i] = convert::To<float, T>(r);                                        \
    }                                                                         \
  }                                                                           \
  template <>                                                                 \
  void Funcname<T, CUDAContext>(                                              \
      const int N, const T* a, const T* b, T* y, CUDAContext* context) {      \
    _Kernel_##T##_##Funcname<<<                                               \
        CAFFE_GET_BLOCKS(N),                                                  \
        CAFFE_CUDA_NUM_THREADS,                                               \
        0,                                                                    \
        context->cuda_stream()>>>(N, a, b, y);                                \
  }

DELEGATE_SIMPLE_CUDA_BINARY_INFIX_FUNCTION(float, Add, +);
DELEGATE_SIMPLE_CUDA_BINARY_INFIX_FUNCTION(int32_t, Add, +);
DELEGATE_SIMPLE_CUDA_BINARY_INFIX_FUNCTION(float, Sub, -);
DELEGATE_SIMPLE_CUDA_BINARY_INFIX_FUNCTION(float, Mul, *);
DELEGATE_SIMPLE_CUDA_BINARY_INFIX_FUNCTION(float, Div, /);

DELEGATE_SIMPLE_CUDA_BINARY_INFIX_FUNCTION(float16, Add, +);
DELEGATE_SIMPLE_CUDA_BINARY_INFIX_FUNCTION(float16, Sub, -);
DELEGATE_SIMPLE_CUDA_BINARY_INFIX_FUNCTION(float16, Mul, *);
DELEGATE_SIMPLE_CUDA_BINARY_INFIX_FUNCTION(float16, Div, /);

#undef DELEGATE_SIMPLE_CUDA_BINARY_INFIX_FUNCTION

#define DELEGATE_SIMPLE_CUDA_BINARY_PREFIX_FUNCTION(T, Funcname, func)    \
  __global__ void _Kernel_##T##_##Funcname(                               \
      const int N, const T* a, const T* b, T* y) {                        \
    CUDA_1D_KERNEL_LOOP(i, N) {                                           \
      float r =                                                           \
          func(convert::To<T, float>(a[i]), convert::To<T, float>(b[i])); \
      y[i] = convert::To<float, T>(r);                                    \
    }                                                                     \
  }                                                                       \
  template <>                                                             \
  void Funcname<T, CUDAContext>(                                          \
      const int N, const T* a, const T* b, T* y, CUDAContext* context) {  \
    _Kernel_##T##_##Funcname<<<                                           \
        CAFFE_GET_BLOCKS(N),                                              \
        CAFFE_CUDA_NUM_THREADS,                                           \
        0,                                                                \
        context->cuda_stream()>>>(N, a, b, y);                            \
  }

DELEGATE_SIMPLE_CUDA_BINARY_PREFIX_FUNCTION(float, ElemwiseMax, fmaxf);

#undef DELEGATE_SIMPLE_CUDA_BINARY_INFIX_FUNCTION

#define DELEGATE_REDUCTION_FUNCTION(T, Funcname, func)                  \
  template <>                                                           \
  void Funcname<T, CUDAContext>(                                        \
      const int N,                                                      \
      const T* src,                                                     \
      T* dst,                                                           \
      Tensor<CUDAContext>* scratch_ptr,                                 \
      CUDAContext* context) {                                           \
    size_t memRequired = 0;                                             \
    cub::DeviceReduce::func(                                            \
        nullptr, memRequired, src, dst, N, context->cuda_stream());     \
    auto buffer_size =                                                  \
        static_cast<TIndex>((memRequired + sizeof(T) - 1) / sizeof(T)); \
    scratch_ptr->Resize(std::vector<TIndex>{buffer_size});              \
    cub::DeviceReduce::func(                                            \
        static_cast<void*>(scratch_ptr->mutable_data<T>()),             \
        memRequired,                                                    \
        src,                                                            \
        dst,                                                            \
        N,                                                              \
        context->cuda_stream());                                        \
  }

DELEGATE_REDUCTION_FUNCTION(float, ReduceMin, Min)
DELEGATE_REDUCTION_FUNCTION(float, ReduceMax, Max)
DELEGATE_REDUCTION_FUNCTION(int32_t, ReduceMax, Max)
DELEGATE_REDUCTION_FUNCTION(int64_t, ReduceMax, Max)


#undef DELEGATE_REDUCTION_FUNCTION

// Caffe2 gemm provides a simpler interface to the gemm functions, with the
// limitation that the data has to be contiguous in memory.
template <>
void Gemm<float, CUDAContext>(
    const CBLAS_TRANSPOSE TransA,
    const CBLAS_TRANSPOSE TransB,
    const int M,
    const int N,
    const int K,
    const float alpha,
    const float* A,
    const float* B,
    const float beta,
    float* C,
    CUDAContext* context,
    TensorProto::DataType math_type) {
  // Note that cublas follows fortran order, so the order is different from
  // the cblas convention.
  int lda = (TransA == CblasNoTrans) ? K : M;
  int ldb = (TransB == CblasNoTrans) ? N : K;
  cublasOperation_t cuTransA =
      (TransA == CblasNoTrans) ? CUBLAS_OP_N : CUBLAS_OP_T;
  cublasOperation_t cuTransB =
      (TransB == CblasNoTrans) ? CUBLAS_OP_N : CUBLAS_OP_T;
  CUBLAS_ENFORCE(cublasSgemm(
      context->cublas_handle(),
      cuTransB,
      cuTransA,
      N,
      M,
      K,
      &alpha,
      B,
      ldb,
      A,
      lda,
      &beta,
      C,
      N));
}

template <>
void Gemm<float16, CUDAContext>(
    const CBLAS_TRANSPOSE TransA,
    const CBLAS_TRANSPOSE TransB,
    const int M,
    const int N,
    const int K,
    const float alpha,
    const float16* A,
    const float16* B,
    const float beta,
    float16* C,
    CUDAContext* context,
    TensorProto::DataType math_type) {
  // Note that cublas follows fortran order, so the order is different from
  // the cblas convention.
  int lda = (TransA == CblasNoTrans) ? K : M;
  int ldb = (TransB == CblasNoTrans) ? N : K;
  cublasOperation_t cuTransA =
      (TransA == CblasNoTrans) ? CUBLAS_OP_N : CUBLAS_OP_T;
  cublasOperation_t cuTransB =
      (TransB == CblasNoTrans) ? CUBLAS_OP_N : CUBLAS_OP_T;
  if (math_type == TensorProto_DataType_FLOAT) {
    CUBLAS_CHECK(cublasSgemmEx(
        context->cublas_handle(),
        cuTransB,
        cuTransA,
        N,
        M,
        K,
        &alpha,
        B,
        CUDA_R_16F,
        ldb,
        A,
        CUDA_R_16F,
        lda,
        &beta,
        C,
        CUDA_R_16F,
        N));

  } else if (math_type == TensorProto_DataType_FLOAT16) {
    // convert alpha, beta from float -> __half
    auto alpha_fp16 = convert::floatToHalf(alpha);
    auto beta_fp16 = convert::floatToHalf(beta);

    // call cublasHgemm
    CUBLAS_CHECK(cublasHgemm(
        context->cublas_handle(),
        cuTransB,
        cuTransA,
        N,
        M,
        K,
        &alpha_fp16,
        (const __half*)B,
        ldb,
        (const __half*)A,
        lda,
        &beta_fp16,
        (__half*)C,
        N));
  } else {
    // fail
    CAFFE_THROW("Unsupported math type");
  }
}

template <>
void GemmBatched<float, CUDAContext>(
    const CBLAS_TRANSPOSE TransA,
    const CBLAS_TRANSPOSE TransB,
    const int batch_size,
    const int M,
    const int N,
    const int K,
    const float alpha,
    const float* A,
    const float* B,
    const float beta,
    float* C,
    CUDAContext* context,
    Tensor<CUDAContext>* scratch,
    TensorProto::DataType math_type) {
  const int a_stride = M * K;
  const int b_stride = K * N;
  const int c_stride = M * N;
#if __CUDACC_VER_MAJOR__ < 8
  // loop over matrices in the batch
  for (int i = 0; i < batch_size; ++i) {
    math::Gemm<float, CUDAContext>(
        TransA,
        TransB,
        M,
        N,
        K,
        alpha,
        A + a_stride * i,
        B + b_stride * i,
        beta,
        C + c_stride * i,
        context);
  }
#else
  // Note that cublas follows fortran order, so the order is different from
  // the cblas convention.
  const int lda = (TransA == CblasNoTrans) ? K : M;
  const int ldb = (TransB == CblasNoTrans) ? N : K;
  cublasOperation_t cuTransA =
      (TransA == CblasNoTrans) ? CUBLAS_OP_N : CUBLAS_OP_T;
  cublasOperation_t cuTransB =
      (TransB == CblasNoTrans) ? CUBLAS_OP_N : CUBLAS_OP_T;
  CUBLAS_ENFORCE(cublasSgemmStridedBatched(
      context->cublas_handle(),
      cuTransB,
      cuTransA,
      N,
      M,
      K,
      &alpha,
      B,
      ldb,
      b_stride,
      A,
      lda,
      a_stride,
      &beta,
      C,
      N,
      c_stride,
      batch_size));
#endif
}

namespace {

__global__ void FloatToHalfKernel(const int N, const float* X, half* Y) {
  CUDA_1D_KERNEL_LOOP(i, N) {
    Y[i] = __float2half(X[i]);
  }
}

__global__ void HalfToFloatKernel(const int N, const half* X, float* Y) {
  CUDA_1D_KERNEL_LOOP(i, N) {
    Y[i] = __half2float(X[i]);
  }
}

};

template <>
void GemmBatched<float16, CUDAContext>(
    const CBLAS_TRANSPOSE TransA,
    const CBLAS_TRANSPOSE TransB,
    const int batch_size,
    const int M,
    const int N,
    const int K,
    const float alpha,
    const float16* A,
    const float16* B,
    const float beta,
    float16* C,
    CUDAContext* context,
    Tensor<CUDAContext>* scratch,
    TensorProto::DataType math_type) {
  const int a_stride = M * K;
  const int b_stride = K * N;
  const int c_stride = M * N;
#if __CUDACC_VER_MAJOR__ < 8
  // loop over matrices in the batch
  for (int i = 0; i < batch_size; ++i) {
    math::Gemm<float16, CUDAContext>(
        TransA,
        TransB,
        M,
        N,
        K,
        alpha,
        A + a_stride * i,
        B + b_stride * i,
        beta,
        C + c_stride * i,
        context);
  }
#else
  // 3 options:
  // 1) scratch != null = cast to fp32, SgemmStridedBatched, cast result to fp16
  // 2) math_type == FLOAT, scratch == nullptr = looped SgemmEx
  // 3) math_type == FLOAT16, scratch == nullptr = batched Hgemm

  if (scratch != nullptr) {
    const int A_size = a_stride * batch_size;
    const int B_size = b_stride * batch_size;
    // cast, cublasSgemmStridedBatched, cast
    size_t in_elems = A_size + B_size;
    size_t out_elems = c_stride * batch_size;

    scratch->Resize(in_elems + out_elems);
    float* scratch_ptr = scratch->mutable_data<float>();

    float* A_fp32 = scratch_ptr;
    float* B_fp32 = scratch_ptr + A_size;
    float* C_fp32 = scratch_ptr + A_size + B_size;

    // cast A, B into fp32
    HalfToFloatKernel<<<CAFFE_GET_BLOCKS(A_size),
                        CAFFE_CUDA_NUM_THREADS,
                        0,
                        context->cuda_stream()>>>(A_size, (half*)A, A_fp32);
    HalfToFloatKernel<<<CAFFE_GET_BLOCKS(B_size),
                        CAFFE_CUDA_NUM_THREADS,
                        0,
                        context->cuda_stream()>>>(B_size, (half*)B, B_fp32);

    // run fp32 batched Gemm
    GemmBatched<float, CUDAContext>(
        TransA,
        TransB,
        batch_size,
        M,
        N,
        K,
        alpha,
        A_fp32,
        B_fp32,
        beta,
        C_fp32,
        context);

    // cast result back to fp16
    FloatToHalfKernel<<<
        CAFFE_GET_BLOCKS(batch_size * M * N),
        CAFFE_CUDA_NUM_THREADS,
        0,
        context->cuda_stream()>>>(batch_size * M * N, C_fp32, (half*)C);
  } else {
    if (math_type == TensorProto_DataType_FLOAT) {
      // loop over matrices in the batch
      for (int i = 0; i < batch_size; ++i) {
        math::Gemm<float16, CUDAContext>(
            TransA,
            TransB,
            M,
            N,
            K,
            alpha,
            A + a_stride * i,
            B + b_stride * i,
            beta,
            C + c_stride * i,
            context);
      }
    } else if (math_type == TensorProto_DataType_FLOAT16) {
      // Note that cublas follows fortran order, so the order is different from
      // the cblas convention.
      const int lda = (TransA == CblasNoTrans) ? K : M;
      const int ldb = (TransB == CblasNoTrans) ? N : K;
      cublasOperation_t cuTransA =
          (TransA == CblasNoTrans) ? CUBLAS_OP_N : CUBLAS_OP_T;
      cublasOperation_t cuTransB =
          (TransB == CblasNoTrans) ? CUBLAS_OP_N : CUBLAS_OP_T;

      // convert alpha, beta from float -> __half
      auto alpha_fp16 = convert::floatToHalf(alpha);
      auto beta_fp16 = convert::floatToHalf(beta);
      CUBLAS_ENFORCE(cublasHgemmStridedBatched(
          context->cublas_handle(),
          cuTransB,
          cuTransA,
          N,
          M,
          K,
          &alpha_fp16,
          (const __half*)B,
          ldb,
          b_stride,
          (const __half*)A,
          lda,
          a_stride,
          &beta_fp16,
          (__half*)C,
          N,
          c_stride,
          batch_size));
    }
  }
#endif
}

#if CUDA_VERSION >= 9000

// No change, but required. Defer to default CUDA engine
template <>
void Gemm<float, CUDAContext, TensorCoreEngine>(
    const CBLAS_TRANSPOSE TransA,
    const CBLAS_TRANSPOSE TransB,
    const int M,
    const int N,
    const int K,
    const float alpha,
    const float* A,
    const float* B,
    const float beta,
    float* C,
    CUDAContext* context,
    TensorProto::DataType math_type) {
  return Gemm<float,CUDAContext>(TransA,
                                 TransB,
                                 M,
                                 N,
                                 K,
                                 alpha,
                                 A,
                                 B,
                                 beta,
                                 C,
                                 context,
                                 math_type);
}

template <>
void Gemm<float16, CUDAContext, TensorCoreEngine>(
    const CBLAS_TRANSPOSE TransA,
    const CBLAS_TRANSPOSE TransB,
    const int M,
    const int N,
    const int K,
    const float alpha,
    const float16* A,
    const float16* B,
    const float beta,
    float16* C,
    CUDAContext* context,
    TensorProto::DataType math_type) {
  // Note that cublas follows fortran order, so the order is different from
  // the cblas convention.
  int lda = (TransA == CblasNoTrans) ? K : M;
  int ldb = (TransB == CblasNoTrans) ? N : K;
  cublasOperation_t cuTransA =
      (TransA == CblasNoTrans) ? CUBLAS_OP_N : CUBLAS_OP_T;
  cublasOperation_t cuTransB =
      (TransB == CblasNoTrans) ? CUBLAS_OP_N : CUBLAS_OP_T;

  // enable TensorCore for this call on this handle
  if (TensorCoreAvailable()) {
    CUBLAS_ENFORCE(cublasSetMathMode(
        context->cublas_handle(),
        CUBLAS_TENSOR_OP_MATH));
  }

  CUBLAS_CHECK(cublasGemmEx(
      context->cublas_handle(),
      cuTransB,
      cuTransA,
      N,
      M,
      K,
      &alpha,
      B,
      CUDA_R_16F,
      ldb,
      A,
      CUDA_R_16F,
      lda,
      &beta,
      C,
      CUDA_R_16F,
      N,
      CUDA_R_32F,
      CUBLAS_GEMM_DFALT_TENSOR_OP));

  // Now disable TensorCore math for subsequent calls to this handle
  if (TensorCoreAvailable()) {
    CUBLAS_ENFORCE(cublasSetMathMode(
        context->cublas_handle(),
        CUBLAS_DEFAULT_MATH));
  }
}

template <>
void GemmBatched<float, CUDAContext, TensorCoreEngine>(
    const CBLAS_TRANSPOSE TransA,
    const CBLAS_TRANSPOSE TransB,
    const int batch_size,
    const int M,
    const int N,
    const int K,
    const float alpha,
    const float* A,
    const float* B,
    const float beta,
    float* C,
    CUDAContext* context,
    Tensor<CUDAContext>* scratch,
    TensorProto::DataType math_type) {
  return GemmBatched<float, CUDAContext, DefaultEngine>(
      TransA,
      TransB,
      batch_size,
      M,
      N,
      K,
      alpha,
      A,
      B,
      beta,
      C,
      context,
      scratch,
      math_type);
}

template <>
void GemmBatched<float16, CUDAContext, TensorCoreEngine>(
    const CBLAS_TRANSPOSE TransA,
    const CBLAS_TRANSPOSE TransB,
    const int batch_size,
    const int M,
    const int N,
    const int K,
    const float alpha,
    const float16* A,
    const float16* B,
    const float beta,
    float16* C,
    CUDAContext* context,
    Tensor<CUDAContext>* scratch,
    TensorProto::DataType math_type) {
  return GemmBatched<float16, CUDAContext, DefaultEngine>(
      TransA,
      TransB,
      batch_size,
      M,
      N,
      K,
      alpha,
      A,
      B,
      beta,
      C,
      context,
      scratch,
      math_type);
}

#endif // CUDA_VERSION >= 9000

template <>
void GemmEx<float, CUDAContext>(
    const CBLAS_TRANSPOSE TransA,
    const CBLAS_TRANSPOSE TransB,
    const int M,
    const int N,
    const int K,
    const float alpha,
    const float* A,
    const int lda,
    const float* B,
    const int ldb,
    const float beta,
    float* C,
    const int ldc,
    CUDAContext* context) {
  // Note that cublas follows fortran order, so the order is different from
  // the cblas convention.
  cublasOperation_t cuTransA =
      (TransA == CblasNoTrans) ? CUBLAS_OP_N : CUBLAS_OP_T;
  cublasOperation_t cuTransB =
      (TransB == CblasNoTrans) ? CUBLAS_OP_N : CUBLAS_OP_T;
  CUBLAS_ENFORCE(cublasSgemm(
      context->cublas_handle(),
      cuTransB,
      cuTransA,
      N,
      M,
      K,
      &alpha,
      B,
      ldb,
      A,
      lda,
      &beta,
      C,
      ldc));
}

template <>
void Gemv<float, CUDAContext>(
    const CBLAS_TRANSPOSE TransA,
    const int M,
    const int N,
    const float alpha,
    const float* A,
    const float* x,
    const float beta,
    float* y,
    CUDAContext* context,
    TensorProto::DataType math_type) {
  cublasOperation_t cuTransA =
      (TransA == CblasNoTrans) ? CUBLAS_OP_T : CUBLAS_OP_N;
  CUBLAS_ENFORCE(cublasSgemv(
      context->cublas_handle(),
      cuTransA,
      N,
      M,
      &alpha,
      A,
      N,
      x,
      1,
      &beta,
      y,
      1));
}

// Batched Add variants
namespace {

template <typename T>
__global__ void AddStripedBatchKernel(
    const int N,
    const T* first,
    T* Y,
    const int stripe,
    const int batch) {
  for (int j = 0; j < batch; j++) {
    const T* x = first + j * stripe;
    CUDA_1D_KERNEL_LOOP(i, N) {
      float tmpY = convert::To<T, float>(Y[i]);
      tmpY += convert::To<T,float>(x[i]);
      Y[i] = convert::To<float,T>(tmpY);
    }
  }
}
} // namespace

#define CAFFE2_SPECIALIZED_CUDA_ADD_STRIPED_BATCH(T)           \
  template <>                                                  \
  void AddStripedBatch<T, CUDAContext>(                        \
      const int N,                                             \
      const T* first,                                          \
      T* Y,                                                    \
      const int stripe,                                        \
      const int batch,                                         \
      CUDAContext* context) {                                  \
    AddStripedBatchKernel<T><<<                                \
        CAFFE_GET_BLOCKS(N),                                   \
        CAFFE_CUDA_NUM_THREADS,                                \
        0,                                                     \
        context->cuda_stream()>>>(N, first, Y, stripe, batch); \
  }

CAFFE2_SPECIALIZED_CUDA_ADD_STRIPED_BATCH(float);
CAFFE2_SPECIALIZED_CUDA_ADD_STRIPED_BATCH(float16);
#undef CAFFE2_SPECIALIZED_CUDA_ADD_STRIPED_BATCH

template <>
void Gemv<float16, CUDAContext>(
    const CBLAS_TRANSPOSE TransA,
    const int M,
    const int N,
    const float alpha,
    const float16* A,
    const float16* x,
    const float beta,
    float16* y,
    CUDAContext* context,
    TensorProto::DataType math_type) {
  cublasOperation_t cuTransA =
      (TransA == CblasNoTrans) ? CUBLAS_OP_T : CUBLAS_OP_N;

  // sort out what we need to call cublasSgemmEx / cublasHgemm
  int m = (cuTransA == CUBLAS_OP_N) ? N : M;
  int k = (cuTransA == CUBLAS_OP_N) ? M : N;
  int LDA = (cuTransA == CUBLAS_OP_N) ? m : k;
  int LDC = m;

  if (math_type == TensorProto_DataType_FLOAT) {
    CUBLAS_CHECK(cublasSgemmEx(
        context->cublas_handle(),
        cuTransA,
        CUBLAS_OP_N,
        m,
        1,
        k,
        &alpha,
        A,
        CUDA_R_16F,
        LDA,
        x,
        CUDA_R_16F,
        k,
        &beta,
        y,
        CUDA_R_16F,
        LDC));
  } else if (math_type == TensorProto_DataType_FLOAT16) {
    auto alpha_fp16 = convert::floatToHalf(alpha);
    auto beta_fp16 = convert::floatToHalf(beta);

    CUBLAS_CHECK(cublasHgemm(
        context->cublas_handle(),
        cuTransA,
        CUBLAS_OP_N,
        m,
        1,
        k,
        &alpha_fp16,
        (const __half*)A,
        LDA,
        (const __half*)x,
        k,
        &beta_fp16,
        (__half*)y,
        LDC));
  } else {
    // fail
    CAFFE_THROW("Unsupported math type");
  }
}

namespace {
template <typename T>
__global__ void SetKernel(const int N, const T alpha, T* Y) {
  CUDA_1D_KERNEL_LOOP(i, N) {
    Y[i] = alpha;
  }
}
} // namespace

#define CAFFE2_SPECIALIZED_CUDA_SET(T)                             \
  template <>                                                      \
  void Set<T, CUDAContext>(                                        \
      const size_t N, const T alpha, T* Y, CUDAContext* context) { \
    SetKernel<<<                                                   \
        CAFFE_GET_BLOCKS(N),                                       \
        CAFFE_CUDA_NUM_THREADS,                                    \
        0,                                                         \
        context->cuda_stream()>>>(N, alpha, Y);                    \
  }

CAFFE2_SPECIALIZED_CUDA_SET(float);
CAFFE2_SPECIALIZED_CUDA_SET(double);
CAFFE2_SPECIALIZED_CUDA_SET(bool);
CAFFE2_SPECIALIZED_CUDA_SET(int8_t);
CAFFE2_SPECIALIZED_CUDA_SET(int16_t);
CAFFE2_SPECIALIZED_CUDA_SET(float16);
CAFFE2_SPECIALIZED_CUDA_SET(int);
CAFFE2_SPECIALIZED_CUDA_SET(int64_t);
CAFFE2_SPECIALIZED_CUDA_SET(char);
CAFFE2_SPECIALIZED_CUDA_SET(uint8_t);
CAFFE2_SPECIALIZED_CUDA_SET(uint16_t);
#undef CAFFE2_SPECIALIZED_CUDA_SET

namespace {
template <typename T>
__global__ void
UniformShift(const size_t N, const float min, const float max, T* x) {
  float scale = max - min;
  CUDA_1D_KERNEL_LOOP(i, N) {
    x[i] = convert::To<float, T>(convert::To<T, float>(x[i]) * scale + min);
  }
}

__global__ void
UniformIntFit(const size_t N, const int min, const int max, unsigned int* x) {
  int* x_int = reinterpret_cast<int*>(x);
  int range = (max - min + 1);
  CUDA_1D_KERNEL_LOOP(i, N) {
    x_int[i] = min + static_cast<int>(x[i] % range);
  }
}
} // namespace

template <>
void RandUniform<float, CUDAContext>(
    const size_t n,
    const float min,
    const float max,
    float* r,
    CUDAContext* context) {
  CURAND_ENFORCE(curandGenerateUniform(context->curand_generator(), r, n));
  UniformShift<float>
      <<<CAFFE_GET_BLOCKS(n),
         CAFFE_CUDA_NUM_THREADS,
         0,
         context->cuda_stream()>>>(n, min, max, r);
}

template <>
void RandUniform<double, CUDAContext>(
    const size_t n,
    const double min,
    const double max,
    double* r,
    CUDAContext* context) {
  CURAND_ENFORCE(
      curandGenerateUniformDouble(context->curand_generator(), r, n));
  UniformShift<double>
      <<<CAFFE_GET_BLOCKS(n),
         CAFFE_CUDA_NUM_THREADS,
         0,
         context->cuda_stream()>>>(n, min, max, r);
}

template <>
void RandUniform<int, CUDAContext>(
    const size_t n,
    const int min,
    const int max,
    int* r,
    CUDAContext* context) {
  CURAND_ENFORCE(curandGenerate(
      context->curand_generator(), reinterpret_cast<unsigned int*>(r), n));
  UniformIntFit<<<
      CAFFE_GET_BLOCKS(n),
      CAFFE_CUDA_NUM_THREADS,
      0,
      context->cuda_stream()>>>(
      n, min, max, reinterpret_cast<unsigned int*>(r));
}

template <typename T>
size_t HandleOddLengthRandGaussian(
    const size_t n,
    const T mean,
    const T std,
    T* r,
    CUDAContext* context) {
  if (n % 2 == 1) {
    std::default_random_engine generator;
    std::normal_distribution<T> distribution(mean, std);
    const T random_value = distribution(generator);
    math::Set<T, CUDAContext>(1, random_value, r + (n - 1), context);
    return n - 1;
  }
  return n;
}

template <>
void RandGaussian<float, CUDAContext>(
    const size_t n,
    const float mean,
    const float std,
    float* r,
    CUDAContext* context) {
  // If n is odd, we add a random Gaussian value at the end manually
  // and generate n-1 random values using curandGenerateNormal.
  // curandGenerateNormal requires n to be even.
  const size_t even_n =
      HandleOddLengthRandGaussian<float>(n, mean, std, r, context);
  CURAND_ENFORCE(
      curandGenerateNormal(context->curand_generator(), r, even_n, mean, std));
}

template <>
void RandGaussian<double, CUDAContext>(
    const size_t n,
    const double mean,
    const double std,
    double* r,
    CUDAContext* context) {
  const size_t even_n =
      HandleOddLengthRandGaussian<double>(n, mean, std, r, context);
  CURAND_ENFORCE(curandGenerateNormalDouble(
      context->curand_generator(), r, even_n, mean, std));
}

template <>
void Dot<float, CUDAContext>(
    const int n,
    const float* a,
    const float* b,
    float* y,
    CUDAContext* context) {
  float result;
  CUBLAS_ENFORCE(cublasSdot(context->cublas_handle(), n, a, 1, b, 1, &result));
  context->Copy<float, CPUContext, CUDAContext>(1, &result, y);
}

template <>
void Dot<float16, CUDAContext>(
    const int n,
    const float16* a,
    const float16* b,
    float16* y,
    CUDAContext* context) {
  float16 result;
  // execute with 32-bit math
  CUBLAS_CHECK(cublasDotEx(
      context->cublas_handle(),
      n,
      a,
      CUDA_R_16F,
      1,
      b,
      CUDA_R_16F,
      1,
      &result,
      CUDA_R_16F,
      CUDA_R_32F));
  context->Copy<float16, CPUContext, CUDAContext>(1, &result, y);
}

// A previous version of caffe2 used Thrust but it turns out that thrust
// reduction has an implicit scratch space allocation and deallocation, which
// may interfere with NCCL and create a deadlock. Hence we are using a custom
// reduction here.
#define SUM_KERNEL_NTHREADS 128
template <typename T>
__global__ void SumKernel(const int N, const T* X, T* Y, bool square) {
  const int idx = threadIdx.x;
  __shared__ float reduction_buffer[SUM_KERNEL_NTHREADS];

  reduction_buffer[idx] = 0;

  // A multilevel reduction.
  // N -> 128
  if (!square) {
    for (int i = idx; i < N; i += SUM_KERNEL_NTHREADS) {
      reduction_buffer[idx] += convert::To<T, float>(X[i]);
    }
  } else {
    for (int i = idx; i < N; i += SUM_KERNEL_NTHREADS) {
      float Xi = convert::To<T, float>(X[i]);
      reduction_buffer[idx] += Xi * Xi;
    }
  }
  __syncthreads();
  // 128 -> 32
  if (idx < 32) {
    reduction_buffer[idx] +=
        reduction_buffer[idx + 32] +
        reduction_buffer[idx + 64] +
        reduction_buffer[idx + 96];
  }
  __syncthreads();
  // 32 -> 1
  if (idx == 0) {
    float tmp = 0;
    for (int i = 0; i < 32; ++i) {
      tmp += reduction_buffer[i];
    }
    *Y = convert::To<float, T>(tmp);
  }
}

// According to the benchmarks script
// caffe2/caffe2/experiments/python/device_reduce_sum_bench.py,
// device reduce is slower for N <= 10000.
#define DEVICE_REDUCE_SIZE_THRESHOLD 10000

namespace {

template <typename T>
__global__ void SumConvertKernel(float* sum, T* dest) {
  *dest = convert::To<float, T>(*sum);
}

template <typename T, typename IterT>
void SumGenericIter(
    const int N,
    IterT it,
    T*& dest,
    CUDAContext* context,
    Tensor<CUDAContext>* scratch_ptr) {
  size_t memRequired = 0;
  cub::DeviceReduce::Sum(
      nullptr, memRequired, it, dest, N, context->cuda_stream());
  auto buffer_size =
      static_cast<TIndex>((memRequired + sizeof(T) - 1) / sizeof(T));
  if (!dest) {
    // allocate one more T at the end of scratch for dest
    scratch_ptr->Resize(std::vector<TIndex>{buffer_size + 1});
    dest = scratch_ptr->template mutable_data<T>() + buffer_size;
  } else {
    scratch_ptr->Resize(std::vector<TIndex>{buffer_size});
  }
  cub::DeviceReduce::Sum(
      static_cast<void*>(scratch_ptr->template mutable_data<T>()),
      memRequired,
      it,
      dest,
      N,
      context->cuda_stream());
}
} // namespace

template <>
void Sum<float, CUDAContext>(
    const int N,
    const float* x,
    float* y,
    CUDAContext* context,
    Tensor<CUDAContext>* scratch_ptr) {
  if (scratch_ptr && N > DEVICE_REDUCE_SIZE_THRESHOLD) {
    SumGenericIter<float>(N, x, y, context, scratch_ptr);
  } else {
    SumKernel<<<1, SUM_KERNEL_NTHREADS, 0, context->cuda_stream()>>>(
      N, x, y, false);
  }
}

template <>
void Sum<int32_t, CUDAContext>(
    const int N,
    const int32_t* x,
    int32_t* y,
    CUDAContext* context,
    Tensor<CUDAContext>* scratch_ptr) {
  if (scratch_ptr && N > DEVICE_REDUCE_SIZE_THRESHOLD) {
    SumGenericIter<int32_t>(N, x, y, context, scratch_ptr);
  } else {
    SumKernel<<<1, SUM_KERNEL_NTHREADS, 0, context->cuda_stream()>>>(
        N, x, y, false);
  }
}

namespace {
template <typename T>
struct FloatTransform {
  inline __host__ __device__ float operator()(const T v) const {
    return convert::To<T, float>(v);
  }
};
} // namespace

#define CAFFE2_MATH_SUM_FUNC(T)                                           \
  template <>                                                             \
  void Sum<T, CUDAContext>(                                               \
      const int N,                                                        \
      const T* x,                                                         \
      T* y,                                                               \
      CUDAContext* context,                                               \
      Tensor<CUDAContext>* scratch_ptr) {                                 \
    if (scratch_ptr && N > DEVICE_REDUCE_SIZE_THRESHOLD) {                \
      FloatTransform<T> transform;                                        \
      cub::TransformInputIterator<float, FloatTransform<T>, const T*> it( \
          x, transform);                                                  \
      float* sum = nullptr;                                               \
      SumGenericIter<float>(N, it, sum, context, scratch_ptr);            \
      SumConvertKernel<<<1, 1, 0, context->cuda_stream()>>>(sum, y);      \
    } else {                                                              \
      SumKernel<<<1, SUM_KERNEL_NTHREADS, 0, context->cuda_stream()>>>(   \
          N, x, y, false);                                                \
    }                                                                     \
  }

CAFFE2_MATH_SUM_FUNC(float16)
#undef CAFFE2_MATH_SUM_FUNC

namespace {
template <typename T>
struct SqrTransform {
  inline __host__ __device__ T operator()(const T v) const {
    return v * v;
  }
};
} //  namespace

template <>
void SumSqr<float, CUDAContext>(
    const int N,
    const float* x,
    float* y,
    CUDAContext* context,
    Tensor<CUDAContext>* scratch_ptr) {
  if (scratch_ptr && N > DEVICE_REDUCE_SIZE_THRESHOLD) {
    SqrTransform<float> transform;
    cub::TransformInputIterator<float, SqrTransform<float>, const float*> it(
        x, transform);
    SumGenericIter<float>(N, it, y, context, scratch_ptr);
  } else {
    SumKernel<<<1, SUM_KERNEL_NTHREADS, 0, context->cuda_stream()>>>(
        N, x, y, true);
  }
}

#define CAFFE2_MATH_SUMSQR_FUNC(T)                                      \
  template <>                                                           \
  void SumSqr<T, CUDAContext>(                                          \
      const int N,                                                      \
      const T* x,                                                       \
      T* y,                                                             \
      CUDAContext* context,                                             \
      Tensor<CUDAContext>* scratch_ptr) {                               \
    if (scratch_ptr && N > DEVICE_REDUCE_SIZE_THRESHOLD) {              \
      FloatTransform<T> float_transform;                                \
      cub::TransformInputIterator<float, FloatTransform<T>, const T*>   \
          float_it(x, float_transform);                                 \
      SqrTransform<float> sqr_transform;                                \
      cub::TransformInputIterator<                                      \
          float,                                                        \
          SqrTransform<float>,                                          \
          decltype(float_it)>                                           \
          it(float_it, sqr_transform);                                  \
      float* sum = nullptr;                                             \
      SumGenericIter<float>(N, it, sum, context, scratch_ptr);          \
      SumConvertKernel<<<1, 1, 0, context->cuda_stream()>>>(sum, y);    \
    } else {                                                            \
      SumKernel<<<1, SUM_KERNEL_NTHREADS, 0, context->cuda_stream()>>>( \
          N, x, y, true);                                               \
    }                                                                   \
  }

CAFFE2_MATH_SUMSQR_FUNC(float16)
#undef CAFFE2_MATH_SUMSQR_FUNC
#undef DEVICE_REDUCE_SIZE_THRESHOLD

namespace {
template <typename T>
__global__ void SelectKernel(
    const int N, const int D, const T* x, const int* idx, T* y) {
  CUDA_1D_KERNEL_LOOP(i, N) {
    y[i] = x[i * D + idx[i]];
  }
}
}  // namespace

template <>
void Select<float, CUDAContext>(
      const int N, const int D, const float* x, const int* idx, float* y,
      CUDAContext* context) {
  SelectKernel<float><<<CAFFE_GET_BLOCKS(N), CAFFE_CUDA_NUM_THREADS,
                        0, context->cuda_stream()>>>(N, D, x, idx, y);
}

template <>
void Select<float16, CUDAContext>(
    const int N,
    const int D,
    const float16* x,
    const int* idx,
    float16* y,
    CUDAContext* context) {
  SelectKernel<float16><<<
      CAFFE_GET_BLOCKS(N),
      CAFFE_CUDA_NUM_THREADS,
      0,
      context->cuda_stream()>>>(N, D, x, idx, y);
}

namespace {
template <typename T>
__global__ void ScaleKernel(const int n, const float alpha, const T* x, T* y) {
  CUDA_1D_KERNEL_LOOP(i, n) {
    // y[i] = convert::To<float,T>(convert::To<T, float>(x[i]) * alpha);
    y[i] = convert::Get<T>(convert::Get<float>(x[i]) * alpha);
  }
}

template <typename T>
__global__ void
ScaleKernelDeviceAlpha(const int n, const float* alpha, const T* x, T* y) {
  CUDA_1D_KERNEL_LOOP(i, n) {
    y[i] = x[i] * (*alpha);
  }
}

template <typename T>
__global__ void PowKernel(const int n, const T* x, const T exponent, T* y) {
  CUDA_1D_KERNEL_LOOP(i, n) {
    y[i] = powf(x[i], exponent);
  }
}

// fp16 specialization
template <>
__global__ void ScaleKernelDeviceAlpha(
    const int n,
    const float* alpha,
    const float16* x,
    float16* y) {
  CUDA_1D_KERNEL_LOOP(i, n) {
    y[i] = convert::To<float, float16>(
        convert::To<float16, float>(x[i]) * (*alpha));
  }
}

}  // namespace

template <>
void Powx<float, CUDAContext>(
    const int N,
    const float* a,
    const float b,
    float* y,
    CUDAContext* context) {
  PowKernel<<<
      CAFFE_GET_BLOCKS(N),
      CAFFE_CUDA_NUM_THREADS,
      0,
      context->cuda_stream()>>>(N, a, b, y);
}

template <>
void Scale<float, CUDAContext>(
    const int n,
    const float alpha,
    const float* x,
    float* y,
    CUDAContext* context) {
  ScaleKernel<float><<<CAFFE_GET_BLOCKS(n), CAFFE_CUDA_NUM_THREADS,
                       0, context->cuda_stream()>>>(n, alpha, x, y);
}

template <>
void Scale<float16, CUDAContext>(
    const int n,
    const float alpha,
    const float16* x,
    float16* y,
    CUDAContext* context) {
  ScaleKernel<float16><<<
      CAFFE_GET_BLOCKS(n),
      CAFFE_CUDA_NUM_THREADS,
      0,
      context->cuda_stream()>>>(n, alpha, x, y);
}

template <>
void Scale<float, CUDAContext>(
    const int n, const float* alpha, const float *x, float* y,
    CUDAContext* context) {
  ScaleKernelDeviceAlpha<float><<<
      CAFFE_GET_BLOCKS(n), CAFFE_CUDA_NUM_THREADS, 0, context->cuda_stream()>>>(
          n, alpha, x, y);
}

template <>
void Scale<float16, CUDAContext>(
    const int n,
    const float* alpha,
    const float16* x,
    float16* y,
    CUDAContext* context) {
  ScaleKernelDeviceAlpha<float16><<<
      CAFFE_GET_BLOCKS(n),
      CAFFE_CUDA_NUM_THREADS,
      0,
      context->cuda_stream()>>>(n, alpha, x, y);
}

template <>
void Axpy<float, CUDAContext>(
    const int N,
    const float alpha,
    const float* X,
    float* Y,
    CUDAContext* context) {
  CUBLAS_ENFORCE(cublasSaxpy(context->cublas_handle(), N, &alpha, X, 1, Y, 1));
}

template <>
void Axpy<double, CUDAContext>(
    const int N,
    const float alpha,
    const double* X,
    double* Y,
    CUDAContext* context) {
  double alpha_d{alpha};
  CUBLAS_ENFORCE(
      cublasDaxpy(context->cublas_handle(), N, &alpha_d, X, 1, Y, 1));
}

template <>
void Axpy<float16, CUDAContext>(
    const int N,
    const float alpha,
    const float16* X,
    float16* Y,
    CUDAContext* context) {
  CUBLAS_CHECK(cublasAxpyEx(
      context->cublas_handle(),
      N,
      &alpha,
      CUDA_R_16F,
      X,
      CUDA_R_16F,
      1,
      Y,
      CUDA_R_16F,
      1,
      CUDA_R_32F));
}

namespace {
template <typename T>
__global__ void AxpyKernel(const int n, const float* a, const T* x, T* y) {
  CUDA_1D_KERNEL_LOOP(index, n) {
    y[index] = convert::Get<T>(
        convert::Get<float>(x[index]) * (*a) + convert::Get<float>(y[index]));
  }
}
}  // namespace

template <>
void Axpy<float, CUDAContext>(
    const int n, const float* alpha, const float* X,
    float* Y, CUDAContext* context) {
  AxpyKernel<float><<<CAFFE_GET_BLOCKS(n), CAFFE_CUDA_NUM_THREADS,
                       0, context->cuda_stream()>>>(n, alpha, X, Y);
}

template <>
void Axpy<float16, CUDAContext>(
    const int n,
    const float* alpha,
    const float16* X,
    float16* Y,
    CUDAContext* context) {
  AxpyKernel<float16><<<
      CAFFE_GET_BLOCKS(n),
      CAFFE_CUDA_NUM_THREADS,
      0,
      context->cuda_stream()>>>(n, alpha, X, Y);
}

namespace {
template <typename T>
__global__ void AxpbyKernel(const int n, const T a, const T* x,
                             const T b, T* y) {
  CUDA_1D_KERNEL_LOOP(index, n) {
    y[index] = x[index] * a + y[index] * b;
  }
}
}  // namespace

template <>
void Axpby<float, CUDAContext>(
    const int n, const float a, const float* x, const float b, float* y,
    CUDAContext* context) {
  AxpbyKernel<float><<<CAFFE_GET_BLOCKS(n), CAFFE_CUDA_NUM_THREADS,
                       0, context->cuda_stream()>>>(n, a, x, b, y);
}

namespace {

template <typename T>
__global__ void im2col_gpu_kernel_nchw(const int n, const T* data_im,
    const int height, const int width, const int kernel_h, const int kernel_w,
    const int dilation_h, const int dilation_w,
    const int pad_t, const int pad_l,
    const int stride_h, const int stride_w,
    const int height_col, const int width_col,
    T* data_col) {

  CUDA_1D_KERNEL_LOOP(index, n) {
    int w_out = index % width_col;
    int h_index = index / width_col;
    int h_out = h_index % height_col;
    int channel_in = h_index / height_col;
    int channel_out = channel_in * kernel_h * kernel_w;
    int h_in = h_out * stride_h - pad_t;
    int w_in = w_out * stride_w - pad_l;
    T* data_col_ptr = data_col;
    data_col_ptr += (channel_out * height_col + h_out) * width_col + w_out;
    const T* data_im_ptr = data_im;
    data_im_ptr += (channel_in * height + h_in) * width + w_in;
    for (int i = 0; i < kernel_h; ++i) {
      for (int j = 0; j < kernel_w; ++j) {
        int h = h_in + i * dilation_h;
        int w = w_in + j * dilation_w;
        *data_col_ptr = (h >= 0 && w >= 0 && h < height && w < width) ?
            data_im_ptr[i * dilation_h * width + j * dilation_w] : 0;
        data_col_ptr += height_col * width_col;
      }
    }
  }
}

template <typename T>
__global__ void im2col_gpu_kernel_nhwc(const int n, const T* data_im,
    const int height, const int width, const int kernel_h, const int kernel_w,
    const int dilation_h, const int dilation_w,
    const int pad_t, const int pad_l,
    const int stride_h, const int stride_w,
    const int width_col, const int channels,
    T* data_col) {

  const int dkernel_h = dilation_h * (kernel_h - 1) + 1;
  const int dkernel_w = dilation_w * (kernel_w - 1) + 1;

  CUDA_1D_KERNEL_LOOP(index, n) {
    int channel_in = index % channels;
    int w_out = index / channels % width_col;
    int h_out = index / channels / width_col;
    int h_in = h_out * stride_h - pad_t;
    int w_in = w_out * stride_w - pad_l;
    T* local_data_col = data_col +
        ((h_out * width_col) + w_out) * channels * kernel_h * kernel_w
        + channel_in;
    for (int i = 0; i < dkernel_h; i += dilation_h) {
      int h = h_in + i;
      for (int j = 0; j < dkernel_w; j += dilation_w) {
        int w = w_in + j;
        *local_data_col = (h >= 0 && w >= 0 && h < height && w < width) ?
            data_im[(h * width + w) * channels + channel_in] : 0;
        local_data_col += channels;
      }
    }
  }
}

template <typename T>
__global__ void col2im_gpu_kernel_nchw(const int n, const T* data_col,
    const int height, const int width,
    const int patch_h, const int patch_w,
    const int dilation_h, const int dilation_w,
    const int pad_t, const int pad_l,
    const int stride_h, const int stride_w,
    const int height_col, const int width_col,
    T* data_im) {

  const int dpatch_h = dilation_h * (patch_h - 1) + 1;
  const int dpatch_w = dilation_w * (patch_w - 1) + 1;

  CUDA_1D_KERNEL_LOOP(index, n) {
    T val = 0;
    int w = index % width + pad_l;
    int h = (index / width) % height + pad_t;
    int c = index / (width * height);

    // compute the start and end of the output
    int w_col_start = (w < dpatch_w) ? 0 : (w - dpatch_w) / stride_w + 1;
    int w_col_end = min(w / stride_w + 1, width_col);
    int h_col_start = (h < dpatch_h) ? 0 : (h - dpatch_h) / stride_h + 1;
    int h_col_end = min(h / stride_h + 1, height_col);

    for (int h_col = h_col_start; h_col < h_col_end; ++h_col) {
      for (int w_col = w_col_start; w_col < w_col_end; ++w_col) {
        int h_k = (h - h_col * stride_h);
        int w_k = (w - w_col * stride_w);
        if (h_k % dilation_h == 0 && w_k % dilation_w == 0) {
          h_k /= dilation_h;
          w_k /= dilation_w;
          int data_col_index =
              (((c * patch_h + h_k) * patch_w + w_k) * height_col + h_col) *
                  width_col +
              w_col;
          val += data_col[data_col_index];
        }
      }
    }
    data_im[index] = val;
  }
}

template <typename T>
__global__ void col2im_gpu_kernel_nhwc(const int n, const T* data_col,
    const int width, const int channels,
    const int patch_h, const int patch_w,
    const int dilation_h, const int dilation_w,
    const int pad_t, const int pad_l,
    const int stride_h, const int stride_w,
    const int height_col, const int width_col,
    T* data_im) {

  const int dpatch_h = dilation_h * (patch_h - 1) + 1;
  const int dpatch_w = dilation_w * (patch_w - 1) + 1;

  CUDA_1D_KERNEL_LOOP(index, n) {
    T val = 0;
    int c = index % channels;
    int w = index / channels % width + pad_l;
    int h = index / channels / width + pad_t;
    // compute the start and end of the output
    int w_col_start = (w < dpatch_w) ? 0 : (w - dpatch_w) / stride_w + 1;
    int w_col_end = min(w / stride_w + 1, width_col);
    int h_col_start = (h < dpatch_h) ? 0 : (h - dpatch_h) / stride_h + 1;
    int h_col_end = min(h / stride_h + 1, height_col);
    int channels_col = patch_h * patch_w * channels;

    for (int h_col = h_col_start; h_col < h_col_end; ++h_col) {
      for (int w_col = w_col_start; w_col < w_col_end; ++w_col) {
        int h_k = h - h_col * stride_h;
        int w_k = w - w_col * stride_w;
        if (h_k % dilation_h == 0 && w_k % dilation_w == 0) {
          h_k /= dilation_h;
          w_k /= dilation_w;
          int c_col = (h_k * patch_w + w_k) * channels + c;
          val += data_col[(h_col * width_col + w_col) * channels_col + c_col];
        }
      }
    }
    data_im[index] = val;
  }
}

// Ported from caffe1
template <typename T, int num_axes>
__global__ void im2col_nd_gpu_kernel(
    const int n,
    const T* data_im,
    const int* im_shape,
    const int* col_shape,
    const int* kernel_shape,
    const int* pad,
    const int* stride,
    const int* dilation,
    T* data_col) {
  int d_offset[num_axes]; // NOLINT(runtime/arrays)
  int d_iter[num_axes]; // NOLINT(runtime/arrays)

  __shared__ int shared_dilation[num_axes];
  __shared__ int shared_kernel_shape[num_axes];
  __shared__ int shared_pad[num_axes];
  __shared__ int shared_stride[num_axes];
  __shared__ int shared_col_shape[num_axes + 1];
  __shared__ int shared_im_shape[num_axes + 1];

  if (threadIdx.x < num_axes) {
    shared_dilation[threadIdx.x] = dilation[threadIdx.x];
    shared_kernel_shape[threadIdx.x] = kernel_shape[threadIdx.x];
    shared_pad[threadIdx.x] = pad[threadIdx.x];
    shared_stride[threadIdx.x] = stride[threadIdx.x];
  }
  if (threadIdx.x < num_axes + 1) {
    shared_col_shape[threadIdx.x] = col_shape[threadIdx.x];
    shared_im_shape[threadIdx.x] = im_shape[threadIdx.x];
  }
  __syncthreads();

  int i;
  int kernel_size = 1;
  for (i = 0; i < num_axes; ++i) {
    kernel_size *= shared_kernel_shape[i];
  }
  CUDA_1D_KERNEL_LOOP(index, n) {
    if (index >= col_shape[0]) {
      break;
    }
    // Initialize offset, computed in the loop below, with intermediate
    // computations used to compute the spatial indices.
    int offset = index;
    for (i = num_axes - 1; i >= 0; --i) {
      if (i < num_axes - 1) {
        offset /= shared_kernel_shape[i + 1];
      }
      d_offset[i] = offset % shared_kernel_shape[i];
    }
    for (i = 0; i < num_axes; ++i) {
      d_iter[i] = 0;
    }
    bool incremented;
    do {
      int index_col = index;
      int index_im = index / kernel_size;
      bool in_range = true;
      for (i = 0; i < num_axes; ++i) {
        const int d = d_iter[i];
        const int d_im = d * shared_stride[i] - shared_pad[i] +
            d_offset[i] * shared_dilation[i];
        in_range &= (d_im >= 0 && d_im < shared_im_shape[i + 1]);

        index_col *= shared_col_shape[i + 1];
        index_col += d;
        index_im *= shared_im_shape[i + 1];
        index_im += d_im;
      }
      if (in_range) {
        // data_col[index_col] = 0;
        data_col[index_col] = data_im[index_im];
        // T temp = data_im[index_im];
      } else {
        data_col[index_col] = 0;
      }

      incremented = false;
      for (i = num_axes - 1; i >= 0; --i) {
        // const int d_max = shared_kernel_shape[i];
        const int d_max = shared_col_shape[i + 1];
        if (d_iter[i] == d_max - 1) {
          d_iter[i] = 0;
        } else { // d_iter[i] < d_max - 1
          ++d_iter[i];
          incremented = true;
          break;
        }
      } // for (int i = num_axes - 1; i >= 0; --i)
    } while (incremented); // do
  } // CUDA_KERNEL_LOOP(index, n)
}

template <typename T, int num_axes>
__global__ void col2im_nd_gpu_kernel(
    const int n,
    const T* data_col,
    const int* im_shape,
    const int* col_shape,
    const int* kernel_shape,
    const int* pad,
    const int* stride,
    const int* dilation,
    T* data_im) {
  int d_im[num_axes]; // NOLINT(runtime/arrays)
  int d_col_iter[num_axes]; // NOLINT(runtime/arrays)
  int d_col_start[num_axes]; // NOLINT(runtime/arrays)
  int d_col_end[num_axes]; // NOLINT(runtime/arrays)

  __shared__ int shared_dilation[num_axes];
  __shared__ int shared_kernel_shape[num_axes];
  __shared__ int shared_pad[num_axes];
  __shared__ int shared_stride[num_axes];
  __shared__ int shared_col_shape[num_axes + 1];
  __shared__ int shared_im_shape[num_axes + 1];

  if (threadIdx.x < num_axes) {
    shared_dilation[threadIdx.x] = dilation[threadIdx.x];
    shared_kernel_shape[threadIdx.x] = kernel_shape[threadIdx.x];
    shared_pad[threadIdx.x] = pad[threadIdx.x];
    shared_stride[threadIdx.x] = stride[threadIdx.x];
  }

  if (threadIdx.x < num_axes + 1) {
    shared_col_shape[threadIdx.x] = col_shape[threadIdx.x];
    shared_im_shape[threadIdx.x] = im_shape[threadIdx.x];
  }
  __syncthreads();

  CUDA_1D_KERNEL_LOOP(index, n) {
    // Initialize channel_in, computed in the loop below, with intermediate
    // computations used to compute the spatial indices.
    int c_im = index;
    // Calculate d_im (image dimensions).
    for (int i = num_axes - 1; i >= 0; --i) {
      d_im[i] = c_im % shared_im_shape[i + 1] + shared_pad[i];
      c_im /= shared_im_shape[i + 1];
    }
    // Calculate col start/end indices.
    bool done = false;
    for (int i = 0; i < num_axes; ++i) {
      const int kernel_extent =
          shared_dilation[i] * (shared_kernel_shape[i] - 1) + 1;
      d_col_start[i] = d_col_iter[i] = (d_im[i] < kernel_extent)
          ? 0
          : (d_im[i] - kernel_extent) / shared_stride[i] + 1;
      d_col_end[i] =
          min(d_im[i] / shared_stride[i] + 1, shared_col_shape[i + 1]);
      if (d_col_start[i] >= d_col_end[i]) {
        // Skip computation if the dimension is 0 at any spatial axis --
        // final val will be 0.
        data_im[index] = 0;
        done = true;
        break; // for (int i = 0; i < num_axes; ++i)
      }
    }
    if (done) {
      continue; // CUDA_KERNEL_LOOP(index, n)
    }
    // Loop over the col to compute the output val.
    T val = 0;
    bool incremented = true;
    bool skip = false;
    do {
      // Compute the final offset.
      int final_offset = 0;
      int kernel_shape_prod = 1;
      int kernel_index;
      for (int i = num_axes - 1; i >= 0; --i) {
        kernel_index = d_im[i] - d_col_iter[i] * shared_stride[i];
        if (kernel_index % shared_dilation[i]) {
          skip = true;
          break;
        } else {
          kernel_index /= shared_dilation[i];
          final_offset += kernel_index * kernel_shape_prod;
          kernel_shape_prod *= shared_kernel_shape[i];
        }
      }
      if (!skip) {
        final_offset += kernel_shape_prod * c_im;
        for (int i = 0; i < num_axes; ++i) {
          final_offset *= shared_col_shape[i + 1];
          final_offset += d_col_iter[i];
        }
        val += data_col[final_offset];
      }
      skip = false;
      incremented = false;
      for (int i = num_axes - 1; i >= 0; --i) {
        const int d_max = d_col_end[i];
        if (d_col_iter[i] == d_max - 1) {
          d_col_iter[i] = d_col_start[i];
        } else { // d_col_iter[i] < d_max - 1
          ++d_col_iter[i];
          incremented = true;
          break; // for (int i = num_axes - 1; i >= 0; --i)
        }
      } // for (int i = num_axes - 1; i >= 0; --i)
    } while (incremented);
    data_im[index] = val;
  } // CUDA_KERNEL_LOOP(index, n)
}

}  // namespace

template <>
void Im2col<float, CUDAContext, StorageOrder::NCHW>(
    const float* data_im, const int channels,
    const int height, const int width, const int kernel_h, const int kernel_w,
    const int dilation_h, const int dilation_w,
    const int pad_t, const int pad_l, const int pad_b, const int pad_r,
    const int stride_h,
    const int stride_w, float* data_col, CUDAContext* context) {

  const int dkernel_h = dilation_h * (kernel_h - 1) + 1;
  const int dkernel_w = dilation_w * (kernel_w - 1) + 1;

  // We are going to launch channels * height_col * width_col kernels, each
  // kernel responsible for copying a single-channel grid.
  int height_col = (height + pad_t + pad_b - dkernel_h) / stride_h + 1;
  int width_col = (width + pad_l + pad_r - dkernel_w) / stride_w + 1;
  int num_kernels = channels * height_col * width_col;
  // NOLINT_NEXT_LINE(whitespace/operators)
  im2col_gpu_kernel_nchw<float><<<CAFFE_GET_BLOCKS(num_kernels),
                                  CAFFE_CUDA_NUM_THREADS, 0,
                                  context->cuda_stream()>>>(
      num_kernels, data_im, height, width, kernel_h, kernel_w,
      dilation_h, dilation_w, pad_t, pad_l, stride_h, stride_w,
      height_col, width_col, data_col);
}

template <>
void Im2col<float, CUDAContext, StorageOrder::NHWC>(
    const float* data_im, const int channels,
    const int height, const int width, const int kernel_h, const int kernel_w,
    const int dilation_h, const int dilation_w,
    const int pad_t, const int pad_l, const int pad_b, const int pad_r,
    const int stride_h,
    const int stride_w, float* data_col, CUDAContext* context) {

  const int dkernel_h = dilation_h * (kernel_h - 1) + 1;
  const int dkernel_w = dilation_w * (kernel_w - 1) + 1;

  // We are going to launch height_col * width_col * channels kernels, each
  // kernel responsible for copying a single-channel grid.
  int height_col = (height + pad_t + pad_b - dkernel_h) / stride_h + 1;
  int width_col = (width + pad_l + pad_r - dkernel_w) / stride_w + 1;
  int num_kernels = height_col * width_col * channels;
  // NOLINT_NEXT_LINE(whitespace/operators)
  im2col_gpu_kernel_nhwc<float><<<CAFFE_GET_BLOCKS(num_kernels),
                                  CAFFE_CUDA_NUM_THREADS, 0,
                                  context->cuda_stream()>>>(
      num_kernels, data_im, height, width, kernel_h, kernel_w,
      dilation_h, dilation_w, pad_t, pad_l, stride_h, stride_w,
      width_col, channels, data_col);
}


template <>
void Col2im<float, CUDAContext, StorageOrder::NCHW>(
    const float* data_col, const int channels,
    const int height, const int width, const int kernel_h, const int kernel_w,
    const int dilation_h, const int dilation_w,
    const int pad_t, const int pad_l, const int pad_b, const int pad_r,
    const int stride_h,
    const int stride_w, float* data_im, CUDAContext* context) {

  const int dkernel_h = dilation_h * (kernel_h - 1) + 1;
  const int dkernel_w = dilation_w * (kernel_w - 1) + 1;

  int height_col = (height + pad_t + pad_b - dkernel_h) / stride_h + 1;
  int width_col = (width + pad_l + pad_r - dkernel_w) / stride_w + 1;
  int num_kernels = channels * height * width;
  // To avoid involving atomic operations, we will launch one kernel per
  // bottom dimension, and then in the kernel add up the top dimensions.
  col2im_gpu_kernel_nchw<float><<<CAFFE_GET_BLOCKS(num_kernels),
                                  CAFFE_CUDA_NUM_THREADS, 0,
                                  context->cuda_stream()>>>(
      num_kernels, data_col, height, width, kernel_h, kernel_w,
      dilation_h, dilation_w,
      pad_t, pad_l, stride_h, stride_w,
      height_col, width_col, data_im);
}

template <>
void Col2im<float, CUDAContext, StorageOrder::NHWC>(
    const float* data_col, const int channels,
    const int height, const int width, const int kernel_h, const int kernel_w,
    const int dilation_h, const int dilation_w,
    const int pad_t, const int pad_l, const int pad_b, const int pad_r,
    const int stride_h,
    const int stride_w, float* data_im, CUDAContext* context) {

  const int dkernel_h = dilation_h * (kernel_h - 1) + 1;
  const int dkernel_w = dilation_w * (kernel_w - 1) + 1;

  int height_col = (height + pad_t + pad_b - dkernel_h) / stride_h + 1;
  int width_col = (width + pad_l + pad_r - dkernel_w) / stride_w + 1;
  int num_kernels = height * width * channels;
  // To avoid involving atomic operations, we will launch one kernel per
  // bottom dimension, and then in the kernel add up the top dimensions.
  col2im_gpu_kernel_nhwc<float><<<CAFFE_GET_BLOCKS(num_kernels),
                                  CAFFE_CUDA_NUM_THREADS, 0,
                                  context->cuda_stream()>>>(
      num_kernels, data_col, width, channels, kernel_h, kernel_w,
      dilation_h, dilation_w,
      pad_t, pad_l, stride_h, stride_w, height_col, width_col, data_im);
}

template <>
void Col2imNd<float, CUDAContext, StorageOrder::NCHW>(
    const float* data_col,
    const int* img_shape,
    const int* col_shape,
    const int img_size,
    const int col_size,
    const int* kernel_shape,
    const int* stride,
    const int* dilation,
    const int* pad,
    const int N,
    float* data_img,
    CUDAContext* context) {
  CAFFE_ENFORCE_LT(
      N, CAFFE_CUDA_NUM_THREADS, "num_axes should be smaller than block size.");

#define COL2IM_ND_KERNEL(n)                                                   \
  col2im_nd_gpu_kernel<float, n> /* NOLINT_NEXT_LINE(whitespace/operators) */ \
      <<<CAFFE_GET_BLOCKS(img_size),                                          \
         CAFFE_CUDA_NUM_THREADS,                                              \
         0,                                                                   \
         context->cuda_stream()>>>(                                           \
          img_size,                                                           \
          data_col,                                                           \
          img_shape,                                                          \
          col_shape,                                                          \
          kernel_shape,                                                       \
          pad,                                                                \
          stride,                                                             \
          dilation,                                                           \
          data_img)

  switch (N) {
    case 1:
      COL2IM_ND_KERNEL(1);
      break;
    case 2:
      COL2IM_ND_KERNEL(2);
      break;
    case 3:
      COL2IM_ND_KERNEL(3);
      break;
    case 4:
      COL2IM_ND_KERNEL(4);
      break;
    case 5:
      COL2IM_ND_KERNEL(5);
      break;
    default:
      CAFFE_THROW(
          "Col2imNd does not support computation with ", N, " spatial axes");
  }
}

template <>
void Im2colNd<float, CUDAContext, StorageOrder::NCHW>(
    const float* data_img,
    const int* img_shape,
    const int* col_shape,
    const int img_size,
    const int col_size,
    const int* kernel_shape,
    const int* stride,
    const int* dilation,
    const int* pad,
    const int N,
    float* data_col,
    CUDAContext* context,
    bool /*accumlate_output*/) {
  CAFFE_ENFORCE_LT(
      N, CAFFE_CUDA_NUM_THREADS, "num_axes should be smaller than block size.");

#define IM2COL_ND_KERNEL(n)                                                   \
  im2col_nd_gpu_kernel<float, n> /* NOLINT_NEXT_LINE(whitespace/operators) */ \
      <<<CAFFE_GET_BLOCKS(col_size),                                          \
         CAFFE_CUDA_NUM_THREADS,                                              \
         0,                                                                   \
         context->cuda_stream()>>>(                                           \
          col_size,                                                           \
          data_img,                                                           \
          img_shape,                                                          \
          col_shape,                                                          \
          kernel_shape,                                                       \
          pad,                                                                \
          stride,                                                             \
          dilation,                                                           \
          data_col)

  switch (N) {
    case 1:
      IM2COL_ND_KERNEL(1);
      break;
    case 2:
      IM2COL_ND_KERNEL(2);
      break;
    case 3:
      IM2COL_ND_KERNEL(3);
      break;
    case 4:
      IM2COL_ND_KERNEL(4);
    case 5:
      IM2COL_ND_KERNEL(5);
      break;
    default:
      CAFFE_THROW(
          "Im2colNd does not support computation with ", N, " spatial axes");
  }
}

template <>
void CopyMatrix<CUDAContext>(
    const size_t itemsize,
    const int M,
    const int N,
    const void* A,
    const int lda,
    void* B,
    const int ldb,
    CUDAContext* context,
    TypeMeta::TypedCopy copy) {
  CAFFE_ENFORCE(!copy, "Copy constructor is not supported in CUDA context");
  cudaMemcpy2DAsync(B, ldb * itemsize, A, lda * itemsize, N * itemsize, M,
                    cudaMemcpyDeviceToDevice, context->cuda_stream());
}

template <>
void CopyVector<float, CUDAContext>(
    const int N,
    const float* src,
    float* dst,
    CUDAContext* context) {
  if (src != dst && N > 0) {
    cudaMemcpyAsync(
        dst,
        src,
        sizeof(float) * N,
        cudaMemcpyDeviceToDevice,
        context->cuda_stream());
  }
}

namespace {
__global__ void rowwise_max_kernel(
    const int rows,
    const int cols,
    const float* data,
    float* out) {
  typedef cub::BlockReduce<float, CAFFE_CUDA_NUM_THREADS> BlockReduce;
  __shared__ typename BlockReduce::TempStorage temp_storage;
  for (int rowIndex = blockIdx.x; rowIndex < rows; rowIndex += gridDim.x) {
    float maxval = -FLT_MAX;
    // NB: The memory accesses here are sequentialized; without unrolling
    // the loop, there will not be any ILP.  However, because we are running
    // this kernel with a lot of threads, this should not be a big problem.
    // However, if we reduce the number of threads to take advantage of
    // warp-wide synchronization, this may become a problem again.
    for (int colIndex = threadIdx.x; colIndex < cols; colIndex += blockDim.x) {
      maxval = max(data[rowIndex * cols + colIndex], maxval);
    }
    maxval = BlockReduce(temp_storage).Reduce(maxval, cub::Max());
    if (threadIdx.x == 0) {
      out[rowIndex] = maxval;
    }
    __syncthreads();
  }
}

__global__ void colwise_max_kernel(
    const int rows,
    const int cols,
    const float* data,
    float* out) {
  typedef cub::BlockReduce<float, CAFFE_CUDA_NUM_THREADS> BlockReduce;
  __shared__ typename BlockReduce::TempStorage temp_storage;
  for (int colIndex = blockIdx.x; colIndex < cols; colIndex += gridDim.x) {
    float maxval = -FLT_MAX;
    for (int rowIndex = threadIdx.x; rowIndex < rows; rowIndex += blockDim.x) {
      maxval = max(data[rowIndex * cols + colIndex], maxval);
    }
    maxval = BlockReduce(temp_storage).Reduce(maxval, cub::Max());
    if (threadIdx.x == 0) {
      out[colIndex] = maxval;
    }
    __syncthreads();
  }
}

} // namespace

template <>
void RowwiseMax(
    const int N,
    const int D,
    const float* x,
    float* y,
    CUDAContext* context) {
  rowwise_max_kernel<<<
      std::min(N, CAFFE_MAXIMUM_NUM_BLOCKS),
      CAFFE_CUDA_NUM_THREADS,
      0,
      context->cuda_stream()>>>(N, D, x, y);
}

template <>
void ColwiseMax(
    const int N,
    const int D,
    const float* x,
    float* y,
    CUDAContext* context) {
  colwise_max_kernel<<<
      std::min(D, CAFFE_MAXIMUM_NUM_BLOCKS),
      CAFFE_CUDA_NUM_THREADS,
      0,
      context->cuda_stream()>>>(N, D, x, y);
}

namespace {
__global__ void
maximum_kernel(const int N, const float alpha, const float* x, float* y) {
  CUDA_1D_KERNEL_LOOP(i, N) {
    y[i] = fmaxf(x[i], alpha);
  }
}
} // namespace

template <>
void Maximum(
    const int N,
    const float alpha,
    const float* x,
    float* y,
    CUDAContext* context) {
  maximum_kernel<<<
      std::min(N, CAFFE_MAXIMUM_NUM_BLOCKS),
      CAFFE_CUDA_NUM_THREADS,
      0,
      context->cuda_stream()>>>(N, alpha, x, y);
}

namespace {

constexpr int kCompileTimeCUDAMaxTransposeDims = 8;

__device__ void ComputeXStride(
    const int num_axes,
    const int* x_dims,
    const int* axes,
    int* x_strides) {
  int buff[kCompileTimeCUDAMaxTransposeDims];
  int cur_stride = 1;
#pragma unroll
  for (int i = num_axes - 1; i >= 0; --i) {
    buff[i] = cur_stride;
#if __CUDA_ARCH__ >= 350
    cur_stride *= __ldg(x_dims + i);
#else
    cur_stride *= x_dims[i];
#endif
  }
#pragma unroll
  for (int i = 0; i < num_axes; ++i) {
#if __CUDA_ARCH__ >= 350
    x_strides[i] = buff[__ldg(axes + i)];
#else
    x_strides[i] = buff[axes[i]];
#endif
  }
}

__device__ int GetXIndex(
    const int num_axes,
    const int* y_dims,
    const int* x_strides,
    int y_index) {
  int x_index = 0;
#pragma unroll
  for (int i = num_axes - 1; i >= 0 && y_index > 0; --i) {
    x_index += (y_index % y_dims[i]) * x_strides[i];
    y_index /= y_dims[i];
  }
  return x_index;
}

template <typename T>
__global__ void TransposeCUDA(
    const int num_axes,
    const int* x_dims,
    const int* y_dims,
    const int* axes,
    const int data_size,
    const T* X,
    T* Y) {
  __shared__ int x_strides[kCompileTimeCUDAMaxTransposeDims];
  __shared__ int y_dims_shared[kCompileTimeCUDAMaxTransposeDims];
  const int tid = threadIdx.x;
  if (tid == 0) {
    ComputeXStride(num_axes, x_dims, axes, x_strides);
  }
  if (tid < num_axes) {
    y_dims_shared[tid] = y_dims[tid];
  }
  __syncthreads();
  CUDA_1D_KERNEL_LOOP(y_index, data_size) {
    const int x_index = GetXIndex(num_axes, y_dims_shared, x_strides, y_index);
#if __CUDA_ARCH__ >= 350
    Y[y_index] = __ldg(X + x_index);
#else
    Y[y_index] = X[x_index];
#endif
  }
}

} // namespace

#define CAFFE2_SPECIALIZED_CUDA_TRANSPOSE(T)                  \
  template <>                                                 \
  void Transpose<T, CUDAContext>(                             \
      const int num_axes,                                     \
      const int* x_dims,                                      \
      const int* y_dims,                                      \
      const int* axes,                                        \
      const int data_size,                                    \
      const T* X,                                             \
      T* Y,                                                   \
      CUDAContext* context) {                                 \
    CAFFE_ENFORCE(                                            \
        num_axes <= kCompileTimeCUDAMaxTransposeDims,         \
        "num_axes exceeds compile time max.");                \
    TransposeCUDA<T>                                          \
        <<<CAFFE_GET_BLOCKS(data_size),                       \
           CAFFE_CUDA_NUM_THREADS,                            \
           0,                                                 \
           context->cuda_stream()>>>(                         \
            num_axes, x_dims, y_dims, axes, data_size, X, Y); \
  }
CAFFE2_SPECIALIZED_CUDA_TRANSPOSE(float)
CAFFE2_SPECIALIZED_CUDA_TRANSPOSE(double)
CAFFE2_SPECIALIZED_CUDA_TRANSPOSE(int)
CAFFE2_SPECIALIZED_CUDA_TRANSPOSE(long)
#undef CAFFE2_SPECIALIZED_CUDA_TRANSPOSE

} // namespace math
} // namespace caffe2

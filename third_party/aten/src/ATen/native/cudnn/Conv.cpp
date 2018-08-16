#include <ATen/ATen.h>
#include <ATen/NativeFunctions.h>
#include <ATen/Config.h>

#if !AT_CUDNN_ENABLED()

namespace at { namespace native {

// See Note [ATen preprocessor philosophy]

at::Tensor cudnn_convolution(
    const at::Tensor& input, const at::Tensor& weight, const at::Tensor& bias /* optional */,
    IntList padding, IntList stride, IntList dilation,
    int64_t groups, bool benchmark, bool deterministic) {
  throw std::runtime_error("cudnn_convolution: ATen not compiled with cuDNN support");
}

at::Tensor cudnn_convolution_backward_input(
    IntList input_size, const at::Tensor& grad_output, const at::Tensor& weight,
    IntList padding, IntList stride, IntList dilation, int64_t groups,
    bool benchmark, bool deterministic) {
  throw std::runtime_error("cudnn_convolution_backward_input: ATen not compiled with cuDNN support");
}

at::Tensor cudnn_convolution_backward_weight(
    IntList weight_size, const at::Tensor& grad_output, const at::Tensor& input,
    IntList padding, IntList stride, IntList dilation, int64_t groups,
    bool benchmark, bool deterministic) {
  throw std::runtime_error("cudnn_convolution_backward_weight: ATen not compiled with cuDNN support");
}

at::Tensor cudnn_convolution_backward_bias(
    const at::Tensor& grad_output) {
  throw std::runtime_error("cudnn_convolution_backward_bias: ATen not compiled with cuDNN support");
}

std::tuple<at::Tensor,at::Tensor,at::Tensor> cudnn_convolution_backward(
    const at::Tensor& input, const at::Tensor& grad_output, const at::Tensor& weight,
    IntList padding, IntList stride, IntList dilation, int64_t groups,
    bool benchmark, bool deterministic, std::array<bool,3> output_mask) {
  throw std::runtime_error("cudnn_convolution_backward: ATen not compiled with cuDNN support");
}

at::Tensor cudnn_convolution_transpose(
    const at::Tensor& input, const at::Tensor& weight, const at::Tensor& bias /* optional */,
    IntList padding, IntList output_padding, IntList stride, IntList dilation,
    int64_t groups, bool benchmark, bool deterministic) {
  throw std::runtime_error("cudnn_convolution_transpose: ATen not compiled with cuDNN support");
}

at::Tensor cudnn_convolution_transpose_backward_input(
    const at::Tensor& grad_output, const at::Tensor& weight,
    IntList padding, IntList stride, IntList dilation,
    int64_t groups, bool benchmark, bool deterministic) {
  throw std::runtime_error("cudnn_convolution_transpose_backward: ATen not compiled with cuDNN support");
}

at::Tensor cudnn_convolution_transpose_backward_weight(
    IntList weight_size, const at::Tensor& grad_output, const at::Tensor& input,
    IntList padding, IntList stride, IntList dilation, int64_t groups,
    bool benchmark, bool deterministic) {
  throw std::runtime_error("cudnn_convolution_transpose_backward_weight: ATen not compiled with cuDNN support");
}

std::tuple<at::Tensor,at::Tensor,at::Tensor> cudnn_convolution_transpose_backward(
    const at::Tensor& input, const at::Tensor& grad_output, const at::Tensor& weight,
    IntList padding, IntList output_padding, IntList stride, IntList dilation, int64_t groups,
    bool benchmark, bool deterministic, std::array<bool,3> output_mask) {
  throw std::runtime_error("cudnn_convolution_transpose_backward: ATen not compiled with cuDNN support");
}

}}

#else  // AT_CUDNN_ENABLED

#include "THC/THC.h"

#include <ATen/cudnn/cudnn-wrapper.h>
#include <ATen/cudnn/Descriptors.h>
#include <ATen/cudnn/Types.h>
#include <ATen/cudnn/Utils.h>

#include <ATen/TensorUtils.h>

#include <functional>
#include <iterator>
#include <sstream>
#include <algorithm>
#include <memory>
#include <mutex>
#include <stdint.h>
#include <unordered_map>

namespace at { namespace native {

// TODO: Go through all the checking code again and make sure
// we haven't missed anything.

// ---------------------------------------------------------------------
//
// Math
//
// ---------------------------------------------------------------------

constexpr int input_batch_size_dim = 0;  // also grad_input
constexpr int input_channels_dim = 1;
constexpr int output_batch_size_dim = 0;  // also grad_output
constexpr int output_channels_dim = 1;
constexpr int weight_output_channels_dim = 0;
constexpr int weight_input_channels_dim = 1;

// Often written as 2 + max_dim (extra dims for batch size and channels)
constexpr int max_dim = 3;

// NB: conv_output_size and conv_input_size are not bijections,
// as conv_output_size loses information; this is why conv_input_size
// takes an extra output_padding argument to resolve the ambiguity.

std::vector<int64_t> conv_output_size(
    IntList input_size, IntList weight_size,
    IntList padding, IntList stride, IntList dilation, int64_t groups
) {
  // ASSERT(input_size.size() > 2)
  // ASSERT(input_size.size() == weight_size.size())
  auto dim = input_size.size();
  std::vector<int64_t> output_size(dim);
  output_size[0] = input_size[input_batch_size_dim];
  output_size[1] = weight_size[weight_output_channels_dim];
  for (size_t d = 2; d < dim; ++d) {
    auto kernel = dilation[d - 2] * (weight_size[d] - 1) + 1;
    output_size[d] = (input_size[d] + (2 * padding[d - 2])
                        - kernel) / stride[d - 2] + 1;
  }
  return output_size;
}

std::vector<int64_t> conv_input_size(
    IntList output_size, IntList weight_size,
    IntList padding, IntList output_padding, IntList stride, IntList dilation, int64_t groups
) {
  // ASSERT(output_size.size() > 2)
  // ASSERT(output_size.size() == weight_size.size())
  auto dim = output_size.size();
  std::vector<int64_t> input_size(dim);
  input_size[0] = output_size[output_batch_size_dim];
  input_size[1] = weight_size[weight_input_channels_dim] * groups;
  for (size_t d = 2; d < dim; ++d) {
    int kernel = dilation[d - 2] * (weight_size[d] - 1) + 1;
    input_size[d] = (output_size[d] - 1) * stride[d - 2] - (2 * padding[d - 2]) +
                     kernel + output_padding[d - 2];
  }
  return input_size;
}

std::vector<int64_t> conv_weight_size(
    IntList input_size, IntList output_size,
    IntList padding, IntList output_padding, IntList stride, IntList dilation, int64_t groups
) {
  auto dim = input_size.size();
  std::vector<int64_t> weight_size(dim);
  weight_size[0] = output_size[1];
  weight_size[1] = input_size[1] / groups;
  for (size_t d = 2; d < dim; ++d) {
    int kernel = input_size[d] - (output_size[d] - 1) * stride[d - 2]
               + 2 * padding[d - 2] - output_padding[d - 2];
    weight_size[d] = (kernel - 1) / dilation[d - 2] + 1;
  }
  return weight_size;
}

// TODO: Move this into the standard library, with a better name?
Tensor narrowGroup(const Tensor& t, int dim, int group_idx, int64_t groups) {
  auto group_size = t.size(dim) / groups;
  return t.narrow(dim, group_idx * group_size, group_size);
}

// ---------------------------------------------------------------------
//
// Checking
//
// ---------------------------------------------------------------------

// Note [Legacy CuDNN grouped convolution support]
// ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
// CuDNN earlier than CuDNN 7 does not directly support group
// convolution, so we provide support for it by sequentially
// running a convolution per group  with appropriately
// adjusted sizes.  https://blog.yani.io/filter-group-tutorial/
// has a fairly good diagram explaining how it works.

// Used on pad, stride and dilation
static void check_args(CheckedFrom c, IntList args, size_t expected_size, const char* arg_name)
{
  if (args.size() > expected_size){
    std::stringstream ss;
    ss << "Too many " << arg_name << " values (" << args.size() << ") supplied, expecting " << expected_size << " (while checking arguments for " << c << ")";
    throw std::runtime_error(ss.str());
  }
  else if (args.size() < expected_size){
    std::stringstream ss;
    ss << "Not enough " << arg_name << " values (" << args.size() << ") supplied, expecting " << expected_size << " (while checking arguments for " << c << ")";
    throw std::runtime_error(ss.str());
  }

  auto num_negative_values = std::count_if(args.begin(), args.end(), [](int x){return x < 0;});
  if (num_negative_values > 0){
    std::stringstream ss;
    ss << arg_name << " should be greater than zero but got (";
    std::copy(args.begin(), args.end() - 1, std::ostream_iterator<int>(ss,", "));
    ss << args.back() <<  ")" << " (while checking arguments for " << c << ")";
    throw std::runtime_error(ss.str());
  }
}


// NB: For many call sites, it is not strictly necessary to check all of
// these relationships (for example, for forward convolution, we compute
// the size of output ourselves, so we don't actually need to check
// output.  However, writing a single function that does everything
// means we get to reuse it for both forwards and all backwards
// variants, even when the set of "real" inputs varies.  The magic of
// relational computing!
//
// (There is one downside, which is that it is slightly harder to write
// error messages which are able to distinguish between real inputs
// (which the user can change) and computed inputs (which the user can
// only indirectly affect).  It would be an interesting exercise to
// come up with a general framework to handle such situations.)
static void convolution_shape_check(
    CheckedFrom c,
    const TensorGeometryArg& input, const TensorGeometryArg& weight, const TensorGeometryArg& output,
    IntList padding, IntList stride, IntList dilation, int64_t groups)
{
  check_args(c, padding, input->dim() - 2, "padding");
  check_args(c, stride, padding.size(), "stride");
  check_args(c, dilation, padding.size(), "dilation");

  // Input
  checkDimRange(c, input, 3, 6 /* exclusive */);
  checkSize(c, input, input_channels_dim, weight->size(1) * groups);

  // Weight
  checkSameDim(c, input, weight);

  // TODO: check that output->size() matches output_sizes
  // TODO: check that weight matches output->sizes()
  checkSameDim(c, input, output);
}

// This POD struct is used to let us easily compute hashes of the
// parameters
struct ConvolutionParams
{
  cudnnDataType_t dataType;
  int input_size[2 + max_dim];
  int input_stride[2 + max_dim];
  int weight_size[2 + max_dim];
  int padding[max_dim];
  int stride[max_dim];
  int dilation[max_dim];
  int64_t groups;
  bool deterministic;
  // NB: transposed purposely omitted: transposed just swaps
  // forward and backward, so you can reuse the benchmark entry,
};
// ConvolutionParams must be a POD because we read out its memory
// contenst as char* when hashing
static_assert(std::is_pod<ConvolutionParams>::value, "ConvolutionParams not POD");

// NB: This can't be a constructor, because then ConvolutionParams
// would not be a POD anymore.
// TODO: Use TensorGeometry here instead of the entire Tensor, which we
// don't actually need.  (OTOH: We can always pass in
// grad_input/grad_output, so this is not very pressing)
void setConvolutionParams(
    ConvolutionParams* params,
    const at::Tensor& input, const at::Tensor& weight,
    IntList padding, IntList stride, IntList dilation,
    int64_t groups, bool deterministic) {

  cudnnDataType_t dataType = getCudnnDataType(input);
  memset(params, 0, sizeof(ConvolutionParams));
  params->dataType = dataType;
  // ASSERT(weight.dim() == input.dim())
  for (int i = 0; i != input.dim(); ++i) {
    params->input_size[i] = (int) input.size(i);
    params->input_stride[i] = (int) input.stride(i);
    params->weight_size[i] = (int) weight.size(i);
  }
  // ASSERT(padding.size() == stride.size())
  // ASSERT(padding.size() == dilation.size())
  for (size_t i = 0; i != padding.size(); ++i) {
    params->padding[i] = padding[i];
    params->stride[i] = stride[i];
    params->dilation[i] = dilation[i];
  }
  // In principle, we shouldn't parametrize by groups for legacy
  // CuDNN, but it doesn't seem worth the effort to actually do this.
  params->groups = groups;
  params->deterministic = deterministic;
}

// Convenience struct for passing around descriptors and data
// pointers
struct ConvolutionArgs {
  cudnnHandle_t handle;
  ConvolutionParams params;
  TensorDescriptor idesc, odesc;
  FilterDescriptor wdesc;
  const Tensor& input, output, weight;
  ConvolutionDescriptor cdesc;

  ConvolutionArgs(const Tensor& input, const Tensor& output, const Tensor& weight) : input(input), output(output), weight(weight) {
  }
};

// ---------------------------------------------------------------------
//
// Benchmarking
//
// ---------------------------------------------------------------------

// Hashing machinery for ConvolutionParams
struct ParamsHash {
  std::size_t operator()(const ConvolutionParams& params) const {
    auto ptr = reinterpret_cast<const uint8_t*>(&params);
    uint32_t value = 0x811C9DC5;
    for (int i = 0; i < (int)sizeof(ConvolutionParams); ++i) {
      value ^= ptr[i];
      value *= 0x01000193;
    }
    return (size_t)value;
  }
};

struct ParamsEqual {
  bool operator()(const ConvolutionParams& a, const ConvolutionParams& b) const {
    auto ptr1 = reinterpret_cast<const uint8_t*>(&a);
    auto ptr2 = reinterpret_cast<const uint8_t*>(&b);
    return memcmp(ptr1, ptr2, sizeof(ConvolutionParams)) == 0;
  }
};

// TODO: Use something less heavy duty than a big honking mutex
template <typename T>
struct BenchmarkCache {
  std::mutex mutex;
  std::unordered_map<ConvolutionParams, T, ParamsHash, ParamsEqual> map;

  bool find(const ConvolutionParams& params, T* results) {
    std::lock_guard<std::mutex> guard(mutex);
    auto it = map.find(params);
    if (it == map.end()) {
      return false;
    }
    *results = it->second;
    return true;
  }

  void insert(const ConvolutionParams& params, const T& results) {
    std::lock_guard<std::mutex> guard(mutex);
    map[params] = results;
  }
};

BenchmarkCache<cudnnConvolutionFwdAlgo_t> fwd_algos;
BenchmarkCache<cudnnConvolutionBwdDataAlgo_t> bwd_data_algos;
BenchmarkCache<cudnnConvolutionBwdFilterAlgo_t> bwd_filter_algos;

// TODO: Stop manually allocating CUDA memory; allocate an ATen byte
// tensor instead.
struct Workspace {
  Workspace(size_t size) : size(size), data(NULL) {
    CUDA_CHECK(THCudaMalloc(globalContext().lazyInitCUDA(), &data, size));
  }
  Workspace(const Workspace&) = delete;
  Workspace(Workspace&&) = default;
  Workspace& operator=(Workspace&&) = default;
  ~Workspace() {
    if (data) {
      THCudaFree(globalContext().lazyInitCUDA(), data);
    }
  }

  size_t size;
  void* data;
};

template<typename algo_t>
struct algorithm_search {
};

cudnnStatus_t getWorkspaceSize(
    const ConvolutionArgs& args,
    cudnnConvolutionFwdAlgo_t algo, size_t* sz)
{
    return cudnnGetConvolutionForwardWorkspaceSize(
        args.handle,
        args.idesc.desc(),
        args.wdesc.desc(),
        args.cdesc.desc(),
        args.odesc.desc(),
        algo,
        sz
    );
}
cudnnStatus_t getWorkspaceSize(
    const ConvolutionArgs& args,
    cudnnConvolutionBwdDataAlgo_t algo, size_t* sz)
{
    return cudnnGetConvolutionBackwardDataWorkspaceSize(
        args.handle,
        args.wdesc.desc(),
        args.odesc.desc(),
        args.cdesc.desc(),
        args.idesc.desc(),
        algo,
        sz);
}
cudnnStatus_t getWorkspaceSize(
    const ConvolutionArgs& args,
    cudnnConvolutionBwdFilterAlgo_t algo, size_t* sz)
{
    return cudnnGetConvolutionBackwardFilterWorkspaceSize(
        args.handle,
        args.idesc.desc(),
        args.odesc.desc(),
        args.cdesc.desc(),
        args.wdesc.desc(),
        algo,
        sz);
}

template<typename algo_t>
size_t getMaxWorkspaceSize(
    const ConvolutionArgs& args,
    const algo_t *algo, int n_algo)
{
    THCState *state = globalContext().lazyInitCUDA();

    size_t max_ws_size = 0;
    size_t max_block_size = 0;
    size_t total_gpu_mem = 0;
    size_t free_gpu_mem = 0;

    THCudaCheck(THCudaMemGetInfoCached(state, &free_gpu_mem, &total_gpu_mem, &max_block_size));

    for (int i = 0; i < n_algo; i++) {
        cudnnStatus_t err;
        size_t sz;
        err = getWorkspaceSize(args, algo[i], &sz);
        if (CUDNN_STATUS_SUCCESS != err || sz == 0
            || sz < max_ws_size || sz > max_block_size) continue;
        max_ws_size = sz;
    }
    return max_ws_size;
}

template<typename perf_t>
perf_t getBestAlgorithm(perf_t *perfResults, bool deterministic, int n_algo) {
  if (deterministic) {
    // iterate over perf results of all algorithms and find the best deterministic algo
    for (int i = 0; i < n_algo; i++) {
      // TODO: Shouldn't all returned results be successful?
      // Double check documentation for cudnnFindConvolutionForwardAlgorithmEx
      if (perfResults[i].status == CUDNN_STATUS_SUCCESS &&
          perfResults[i].determinism == CUDNN_DETERMINISTIC) {
        return perfResults[i];
      }
    }
    throw std::runtime_error("no deterministic convolution algorithms available in CuDNN");
  } else {
    return perfResults[0];
  }
}

template<>
struct algorithm_search<cudnnConvolutionFwdAlgo_t> {
  using perf_t = cudnnConvolutionFwdAlgoPerf_t;
  using algo_t = cudnnConvolutionFwdAlgo_t;

  static constexpr auto DEFAULT_ALGO = CUDNN_CONVOLUTION_FWD_ALGO_IMPLICIT_PRECOMP_GEMM;
  static BenchmarkCache<algo_t>& cache() { return fwd_algos; }

  static perf_t findAlgorithm(const ConvolutionArgs& args) {
    static const algo_t algos[] = {
         CUDNN_CONVOLUTION_FWD_ALGO_GEMM,
         CUDNN_CONVOLUTION_FWD_ALGO_FFT,
         CUDNN_CONVOLUTION_FWD_ALGO_FFT_TILING,
         CUDNN_CONVOLUTION_FWD_ALGO_IMPLICIT_GEMM,
         CUDNN_CONVOLUTION_FWD_ALGO_IMPLICIT_PRECOMP_GEMM,
         CUDNN_CONVOLUTION_FWD_ALGO_DIRECT,
         CUDNN_CONVOLUTION_FWD_ALGO_WINOGRAD,
         CUDNN_CONVOLUTION_FWD_ALGO_WINOGRAD_NONFUSED,
    };
    static constexpr int num_algos = CUDNN_CONVOLUTION_FWD_ALGO_COUNT;
    static_assert(sizeof(algos) / sizeof(algos[0]) == num_algos,
                  "Missing cuDNN convolution forward algorithms");
    int perf_count;
    std::unique_ptr<perf_t[]> perf_results(new perf_t[num_algos]);
    size_t max_ws_size = getMaxWorkspaceSize(args, algos, num_algos);
    Workspace ws(max_ws_size);
    CUDNN_CHECK(cudnnFindConvolutionForwardAlgorithmEx(
        args.handle,
        args.idesc.desc(), args.input.data_ptr(),
        args.wdesc.desc(), args.weight.data_ptr(),
        args.cdesc.desc(),
        args.odesc.desc(), args.output.data_ptr(),
        num_algos,
        &perf_count,
        perf_results.get(),
        ws.data,
        ws.size));
    return getBestAlgorithm(perf_results.get(), args.params.deterministic, perf_count);
  }

  static void getAlgorithm(
    const ConvolutionArgs& args,
    algo_t* algo)
  {
    cudnnConvolutionFwdPreference_t pref = CUDNN_CONVOLUTION_FWD_PREFER_FASTEST;
    CUDNN_CHECK(cudnnGetConvolutionForwardAlgorithm(
        args.handle,
        args.idesc.desc(),
        args.wdesc.desc(),
        args.cdesc.desc(),
        args.odesc.desc(),
        pref,
        0,
        algo));
  }

  static void getWorkspaceSize(
    const ConvolutionArgs& args,
    algo_t algo, size_t* workspaceSize)
  {
    CUDNN_CHECK(cudnnGetConvolutionForwardWorkspaceSize(
        args.handle,
        args.idesc.desc(),
        args.wdesc.desc(),
        args.cdesc.desc(),
        args.odesc.desc(),
        algo,
        workspaceSize));
  }
};

template<>
struct algorithm_search<cudnnConvolutionBwdDataAlgo_t> {
  using perf_t = cudnnConvolutionBwdDataAlgoPerf_t;
  using algo_t = cudnnConvolutionBwdDataAlgo_t;

  static constexpr auto DEFAULT_ALGO = CUDNN_CONVOLUTION_BWD_DATA_ALGO_1;
  static BenchmarkCache<algo_t>& cache() { return bwd_data_algos; }

  static perf_t findAlgorithm(const ConvolutionArgs& args) {
    static const algo_t algos[] = {
        CUDNN_CONVOLUTION_BWD_DATA_ALGO_0,
        CUDNN_CONVOLUTION_BWD_DATA_ALGO_1,
        CUDNN_CONVOLUTION_BWD_DATA_ALGO_FFT,
        CUDNN_CONVOLUTION_BWD_DATA_ALGO_FFT_TILING,
        CUDNN_CONVOLUTION_BWD_DATA_ALGO_WINOGRAD,
        CUDNN_CONVOLUTION_BWD_DATA_ALGO_WINOGRAD_NONFUSED
    };
    static constexpr int num_algos = CUDNN_CONVOLUTION_BWD_DATA_ALGO_COUNT;
    static_assert(sizeof(algos) / sizeof(algos[0]) == num_algos,
                  "Missing cuDNN convolution backward data algorithms.");
    int perf_count;
    std::unique_ptr<perf_t[]> perf_results(new perf_t[num_algos]);
    size_t max_ws_size = getMaxWorkspaceSize(args, algos, num_algos);
    Workspace ws(max_ws_size);
    CUDNN_CHECK(cudnnFindConvolutionBackwardDataAlgorithmEx(
        args.handle,
        args.wdesc.desc(), args.weight.data_ptr(),
        args.odesc.desc(), args.output.data_ptr(),
        args.cdesc.desc(),
        args.idesc.desc(), args.input.data_ptr(),
        num_algos,
        &perf_count,
        perf_results.get(),
        ws.data,
        ws.size));
    return getBestAlgorithm(perf_results.get(), args.params.deterministic, perf_count);
  }

  static void getAlgorithm(const ConvolutionArgs& args, algo_t* algo) {
    CUDNN_CHECK(cudnnGetConvolutionBackwardDataAlgorithm(
        args.handle,
        args.wdesc.desc(),
        args.odesc.desc(),
        args.cdesc.desc(),
        args.idesc.desc(),
        CUDNN_CONVOLUTION_BWD_DATA_PREFER_FASTEST,
        0,
        algo));
  }

  static void getWorkspaceSize(
    const ConvolutionArgs& args,
    cudnnConvolutionBwdDataAlgo_t algo, size_t* workspaceSize)
  {
    CUDNN_CHECK(cudnnGetConvolutionBackwardDataWorkspaceSize(
        args.handle,
        args.wdesc.desc(),
        args.odesc.desc(),
        args.cdesc.desc(),
        args.idesc.desc(),
        algo,
        workspaceSize));
  }
};

template<>
struct algorithm_search<cudnnConvolutionBwdFilterAlgo_t> {
  using perf_t = cudnnConvolutionBwdFilterAlgoPerf_t;
  using algo_t = cudnnConvolutionBwdFilterAlgo_t;

  static constexpr auto DEFAULT_ALGO = CUDNN_CONVOLUTION_BWD_FILTER_ALGO_1;

  static BenchmarkCache<algo_t>& cache() { return bwd_filter_algos; }

  static perf_t findAlgorithm(const ConvolutionArgs& args) {
    static const algo_t algos[] = {
        CUDNN_CONVOLUTION_BWD_FILTER_ALGO_0,
        CUDNN_CONVOLUTION_BWD_FILTER_ALGO_1,
        CUDNN_CONVOLUTION_BWD_FILTER_ALGO_FFT,
        CUDNN_CONVOLUTION_BWD_FILTER_ALGO_3,
        CUDNN_CONVOLUTION_BWD_FILTER_ALGO_WINOGRAD_NONFUSED,
#if CUDNN_VERSION >= 6000
        CUDNN_CONVOLUTION_BWD_FILTER_ALGO_FFT_TILING,
#endif
    };
    // NOTE: - 1 because ALGO_WINOGRAD is not implemented
    static constexpr int num_algos = CUDNN_CONVOLUTION_BWD_FILTER_ALGO_COUNT - 1;
    static_assert(sizeof(algos) / sizeof(algos[0]) == num_algos,
                  "Missing cuDNN convolution backward filter algorithms.");
    std::unique_ptr<perf_t[]> perf_results(new perf_t[num_algos]);
    size_t max_ws_size = getMaxWorkspaceSize(args, algos, num_algos);
    int perf_count;
    Workspace ws(max_ws_size);

    CUDNN_CHECK(cudnnFindConvolutionBackwardFilterAlgorithmEx(
        args.handle,
        args.idesc.desc(), args.input.data_ptr(),
        args.odesc.desc(), args.output.data_ptr(),
        args.cdesc.desc(),
        args.wdesc.desc(), args.weight.data_ptr(),
        num_algos,
        &perf_count,
        perf_results.get(),
        ws.data,
        ws.size));
    return getBestAlgorithm<perf_t>(perf_results.get(), args.params.deterministic, perf_count);
  }

  static void getAlgorithm(const ConvolutionArgs& args, algo_t* algo) {
    CUDNN_CHECK(cudnnGetConvolutionBackwardFilterAlgorithm(
        args.handle,
        args.idesc.desc(),
        args.odesc.desc(),
        args.cdesc.desc(),
        args.wdesc.desc(),
        CUDNN_CONVOLUTION_BWD_FILTER_PREFER_FASTEST,
        0,
        algo)
    );
  }

  static void getWorkspaceSize(const ConvolutionArgs& args, algo_t algo, size_t* workspaceSize)
  {
    CUDNN_CHECK(cudnnGetConvolutionBackwardFilterWorkspaceSize(
        args.handle,
        args.idesc.desc(),
        args.odesc.desc(),
        args.cdesc.desc(),
        args.wdesc.desc(),
        algo,
        workspaceSize));
  }
};

template<typename algo_t>
void findAlgorithm(const ConvolutionArgs& args, bool benchmark, algo_t* algo) {
  using search = algorithm_search<algo_t>;
  auto& cache = search::cache();

  if (cache.find(args.params, algo)) {
    return;
  }

  if (args.params.deterministic && !benchmark) {
    *algo = search::DEFAULT_ALGO;
    return;
  }

  if (!benchmark) {
    search::getAlgorithm(args, algo);
    return;
  }

  if (cache.find(args.params, algo)) {
    // re-check cache since another thread may have benchmarked the algorithm
    return;
  }

  auto perfResults = search::findAlgorithm(args);
  // for deterministic algo, look at all the perf results and return the best
  // deterministic algo
  if (perfResults.status == CUDNN_STATUS_SUCCESS &&
      !(args.params.deterministic && perfResults.determinism != CUDNN_DETERMINISTIC)) {
      *algo = perfResults.algo;
  } else {
      *algo = search::DEFAULT_ALGO;
  }
  cache.insert(args.params, *algo);

  THCDeviceAllocator* allocator = THCCachingAllocator_get();
  CUDA_CHECK(allocator->emptyCache(allocator->state));
}

template<typename algo_t>
Workspace chooseAlgorithm(
    const ConvolutionArgs& args,
    bool benchmark,
    algo_t* algo)
{
  findAlgorithm(args, benchmark, algo);

  using search = algorithm_search<algo_t>;
  size_t workspace_size;
  search::getWorkspaceSize(args, *algo, &workspace_size);
  try {
    return Workspace(workspace_size);
  } catch (std::runtime_error& e) {
    cudaGetLastError(); // clear OOM error

    // switch to default algorithm and record it in the cache to prevent
    // further OOM errors
    *algo = search::DEFAULT_ALGO;
    search::cache().insert(args.params, *algo);

    search::getWorkspaceSize(args, *algo, &workspace_size);
    return Workspace(workspace_size);
  }
}

// ---------------------------------------------------------------------
//
// Bias addition
//
// ---------------------------------------------------------------------

// In-place!
void cudnn_convolution_add_bias_(CheckedFrom c, const TensorArg& output, const TensorArg& bias)
{
  checkAllSameType(c, {output, bias});
  checkAllSameGPU(c, {output, bias});
  checkSize(c, bias, { output->size(output_channels_dim) });

  // See Note [CuDNN broadcast padding].  Handle the left padding
  // ourselves, but use TensorDescriptor's padding argument to do the rest.
  TensorDescriptor bdesc, odesc;
  bdesc.set(bias->expand({1, bias->size(0)}), output->dim());
  odesc.set(*output);

  auto handle = getCudnnHandle();
  auto dataType = getCudnnDataType(*bias);
  Constant one(dataType, 1);

  CUDNN_CHECK(cudnnAddTensor(handle, &one, bdesc.desc(), bias->data_ptr(),
                                     &one, odesc.desc(), output->data_ptr()));
}

// The general strategy:
//
//    - cudnn_convolution (Tensor)
//      Entry points for clients, takes bias
//
//    - cudnn_convolution_forward (TensorArg)
//      Entry point, which may be reused between regular
//      convolution and transposed convolution.  Does NOT take bias.
//
//    - raw_cudnn_convolution_forward_out (Tensor)
//      Low level function which invokes CuDNN, and takes an output
//      tensor which is directly written to (thus _out).
//
// Where does argument checking happen?  Here's the division of
// responsibility:
//  - Things that happen in at::Tensor
//    - TensorArg allocation
//    - setCuDNNStreamToCurrent
//  - Things that happen in TensorArg
//    - Check arguments (type, GPU, shape)
//
// TODO: Consider renaming zero-indexed arguments to "self"



// ---------------------------------------------------------------------
//
// Convolution forward / Transposed convolution backward
//
// ---------------------------------------------------------------------

// The raw API directly invokes CuDNN and does not emulate support
// for group convolution on old versions of CuDNN.
//
// There are a few reasons this should never be directly exposed
// via ATen:
//
//    - It takes output as a parameter (this should be computed!)
//    - It doesn't do input checking
//    - It doesn't resize output (it is assumed to be correctly sized)
//    - It takes a ConvolutionParams struct
//
void raw_cudnn_convolution_forward_out(
    const Tensor& output, const Tensor& input, const Tensor& weight,
    IntList padding, IntList stride, IntList dilation, int64_t groups,
    bool benchmark, bool deterministic) {

  auto dataType = getCudnnDataType(input);

  ConvolutionArgs args{ input, output, weight };
  args.handle = getCudnnHandle();
  setConvolutionParams(&args.params, input, weight, padding, stride, dilation, groups, deterministic);
  args.idesc.set(input);
  args.wdesc.set(weight);
  args.odesc.set(output);
  args.cdesc.set(dataType, input.dim() - 2, args.params.padding, args.params.stride, args.params.dilation, args.params.groups);

  // TODO: when we do legacy group convolution support, we'll repeatedly
  // reinitialize the workspace for each convolution we do.  This is
  // wasteful; we'd rather reuse the workspace.  OTOH, legacy group
  // convolution support is already pretty slow, so this might not
  // matter.  (This applies to raw_cudnn_convolution_backward_input as well.)
  cudnnConvolutionFwdAlgo_t fwdAlg;
  Workspace workspace = chooseAlgorithm(args, benchmark, &fwdAlg);

  Constant one(dataType, 1);
  Constant zero(dataType, 0);

  CUDNN_CHECK(cudnnConvolutionForward(
    args.handle,
    &one, args.idesc.desc(), input.data_ptr(),
    args.wdesc.desc(), weight.data_ptr(),
    args.cdesc.desc(), fwdAlg, workspace.data, workspace.size,
    &zero, args.odesc.desc(), output.data_ptr()));
}

Tensor cudnn_convolution_forward(
    CheckedFrom c,
    const TensorArg& input, const TensorArg& weight,
    IntList padding, IntList stride, IntList dilation, int64_t groups,
    bool benchmark, bool deterministic)
{
  checkAllSameType(c, {input, weight});
  checkAllSameGPU(c, {input, weight});

  auto output_t = input->type().tensor(
                    conv_output_size(input->sizes(), weight->sizes(),
                                     padding, stride, dilation, groups));

  // Avoid ambiguity of "output" when this is being used as backwards
  TensorArg output{ output_t, "result", 0 };
  convolution_shape_check(c, input, weight, output, padding, stride, dilation, groups);

  // See #4500
  Tensor weight_contig = weight->contiguous();

#if CUDNN_VERSION < 7000
  for (int i = 0; i < groups; i++) {
    raw_cudnn_convolution_forward_out(
        narrowGroup(*output, output_channels_dim,        i, groups),
        narrowGroup(*input,  input_channels_dim,         i, groups),
        narrowGroup(weight_contig, weight_output_channels_dim, i, groups),
        padding, stride, dilation, 1, benchmark, deterministic);
  }
#else
  raw_cudnn_convolution_forward_out(
      *output, *input, weight_contig,
      padding, stride, dilation, groups, benchmark, deterministic);
#endif

  return *output;
}

Tensor cudnn_convolution(
    const Tensor& input_t, const Tensor& weight_t, const Tensor& bias_t,
    IntList padding, IntList stride, IntList dilation,
    int64_t groups, bool benchmark, bool deterministic)
{
  TensorArg input  { input_t,  "input",  1 },
            weight { weight_t, "weight", 2 },
            bias   { bias_t,   "bias",   3 };
  setCuDNNStreamToCurrent();
  CheckedFrom c = "cudnn_convolution";
  auto output_t = cudnn_convolution_forward(
    c, input, weight, padding, stride, dilation, groups, benchmark, deterministic);
  if (bias->defined()) {
    cudnn_convolution_add_bias_(c, { output_t, "result", 0 }, bias);
  }
  return output_t;
}

// NB: output_padding not needed here, as there is no ambiguity to
// resolve
Tensor cudnn_convolution_transpose_backward_input(
    const Tensor& grad_output_t, const Tensor& weight_t,
    IntList padding, IntList stride, IntList dilation,
    int64_t groups, bool benchmark, bool deterministic)
{
  TensorArg grad_output { grad_output_t,  "grad_output", 1 },
            weight      { weight_t, "weight", 2 };
  setCuDNNStreamToCurrent();
  return cudnn_convolution_forward(
    "cudnn_convolution_transpose_backward_input",
    grad_output, weight, padding, stride, dilation, groups, benchmark, deterministic);
}

std::tuple<at::Tensor,at::Tensor,at::Tensor> cudnn_convolution_transpose_backward(
    const at::Tensor& input, const at::Tensor& grad_output_t, const at::Tensor& weight,
    IntList padding, IntList output_padding, IntList stride, IntList dilation, int64_t groups,
    bool benchmark, bool deterministic, std::array<bool,3> output_mask) {

  Tensor grad_output = grad_output_t.contiguous();

  Tensor grad_input, grad_weight, grad_bias;
  if (output_mask[0]) {
    grad_input = at::cudnn_convolution_transpose_backward_input(grad_output, weight, padding, stride, dilation, groups, benchmark, deterministic);
  }
  if (output_mask[1]) {
    grad_weight = at::cudnn_convolution_transpose_backward_weight(weight.sizes(), grad_output, input, padding, stride, dilation, groups, benchmark, deterministic);
  }
  if (output_mask[2]) {
    grad_bias = at::cudnn_convolution_backward_bias(grad_output);
  }

  return std::tuple<Tensor,Tensor,Tensor>{grad_input, grad_weight, grad_bias};
}

// ---------------------------------------------------------------------
//
// Convolution backward / Transposed convolution forward
//
// ---------------------------------------------------------------------

void raw_cudnn_convolution_backward_input_out(
    const at::Tensor& grad_input,
    const at::Tensor& grad_output,
    const at::Tensor& weight,
    IntList padding, IntList stride, IntList dilation, int64_t groups,
    bool benchmark, bool deterministic) {

  auto dataType = getCudnnDataType(grad_output);

  ConvolutionArgs args{ grad_input, grad_output, weight };
  args.handle = getCudnnHandle();
  setConvolutionParams(&args.params, grad_input, weight, padding, stride, dilation, groups, deterministic);
  args.idesc.set(grad_input);
  args.wdesc.set(weight);
  args.odesc.set(grad_output);
  args.cdesc.set(dataType, grad_output.dim() - 2, args.params.padding, args.params.stride, args.params.dilation, args.params.groups);

  cudnnConvolutionBwdDataAlgo_t bwdDataAlg;
  Workspace workspace = chooseAlgorithm(args, benchmark, &bwdDataAlg);

  Constant one(dataType, 1);
  Constant zero(dataType, 0);

  CUDNN_CHECK(cudnnConvolutionBackwardData(
      args.handle,
      &one, args.wdesc.desc(), weight.data_ptr(),
      args.odesc.desc(), grad_output.data_ptr(),
      args.cdesc.desc(), bwdDataAlg, workspace.data, workspace.size,
      &zero, args.idesc.desc(), grad_input.data_ptr()));
}

// Backward and transpose are algorithmically equivalent, but they
// compute their geometry differently.  In a backwards, you knew what
// the original size of the input tensor was, so you can cache that
// geometry and fill it directly.  In transposed convolution, it is
// more conventional to not explicitly specify the output (previously
// input) size, and compute it.  This, however, leaves a degree of
// freedom; this degree of freedom is resolved using the
// output_padding parameter.  Both of these interfaces are equivalent,
// but they are differently convenient depending on the use case.

Tensor cudnn_convolution_backward_input(
    CheckedFrom c,
    IntList input_size, const TensorArg& grad_output, const TensorArg& weight,
    IntList padding, IntList stride, IntList dilation, int64_t groups,
    bool benchmark, bool deterministic)
{
  checkAllSameType(c, {grad_output, weight});
  checkAllSameGPU(c, {grad_output, weight});

  auto grad_input_t = grad_output->type().tensor(input_size);

  // Avoid "grad_input" when this is being used as transposed convolution
  TensorArg grad_input{ grad_input_t, "result", 0 };
  convolution_shape_check(c, grad_input, weight, grad_output, padding, stride, dilation, groups);

  // See #4500
  Tensor weight_contig = weight->contiguous();

#if CUDNN_VERSION < 7000
  for (int i = 0; i < groups; i++) {
    raw_cudnn_convolution_backward_input_out(
        narrowGroup(*grad_input, input_channels_dim, i, groups),
        narrowGroup(*grad_output, output_channels_dim, i, groups),
        narrowGroup(weight_contig, weight_output_channels_dim, i, groups),
        padding, stride, dilation, 1, benchmark, deterministic);
  }
#else
  raw_cudnn_convolution_backward_input_out(
      *grad_input, *grad_output, weight_contig,
      padding, stride, dilation, groups, benchmark, deterministic);
#endif

  return *grad_input;
}

Tensor cudnn_convolution_transpose_forward(
    CheckedFrom c,
    const TensorArg& grad_output, const TensorArg& weight,
    IntList padding, IntList output_padding, IntList stride, IntList dilation, int64_t groups,
    bool benchmark, bool deterministic)
{
  auto input_size = conv_input_size(grad_output->sizes(), weight->sizes(),
                                    padding, output_padding, stride, dilation, groups);
  return cudnn_convolution_backward_input(c, input_size, grad_output, weight,
                                    padding, stride, dilation, groups, benchmark, deterministic);
}

Tensor cudnn_convolution_backward_input(
    IntList input_size, const Tensor& grad_output_t, const Tensor& weight_t,
    IntList padding, IntList stride, IntList dilation, int64_t groups,
    bool benchmark, bool deterministic)
{
  TensorArg grad_output{ grad_output_t, "grad_output", 1 },
            weight{ weight_t, "weight", 2 };
  setCuDNNStreamToCurrent();
  return cudnn_convolution_backward_input(
      "cudnn_convolution_backward_input",
      input_size, grad_output, weight,
      padding, stride, dilation, groups, benchmark, deterministic);
}

std::tuple<at::Tensor,at::Tensor,at::Tensor> cudnn_convolution_backward(
    const at::Tensor& input, const at::Tensor& grad_output_t, const at::Tensor& weight,
    IntList padding, IntList stride, IntList dilation, int64_t groups,
    bool benchmark, bool deterministic, std::array<bool,3> output_mask) {

  Tensor grad_output = grad_output_t.contiguous();

  Tensor grad_input, grad_weight, grad_bias;
  if (output_mask[0]) {
    grad_input = at::cudnn_convolution_backward_input(input.sizes(), grad_output, weight, padding, stride, dilation, groups, benchmark, deterministic);
  }
  if (output_mask[1]) {
    grad_weight = at::cudnn_convolution_backward_weight(weight.sizes(), grad_output, input, padding, stride, dilation, groups, benchmark, deterministic);
  }
  if (output_mask[2]) {
    grad_bias = at::cudnn_convolution_backward_bias(grad_output);
  }

  return std::tuple<Tensor,Tensor,Tensor>{grad_input, grad_weight, grad_bias};
}

Tensor cudnn_convolution_transpose(
    const Tensor& input_t, const Tensor& weight_t, const Tensor& bias_t,
    IntList padding, IntList output_padding, IntList stride, IntList dilation,
    int64_t groups, bool benchmark, bool deterministic)
{
  TensorArg input  { input_t,  "input",  1 },
            weight { weight_t, "weight", 2 },
            bias   { bias_t,   "bias",   3 };
  CheckedFrom c = "cudnn_convolution_transpose";
  auto output_t = cudnn_convolution_transpose_forward(
    c, input, weight, padding, output_padding, stride, dilation, groups, benchmark, deterministic);
  if (bias->defined()) {
    cudnn_convolution_add_bias_(c, { output_t, "result", 0 }, bias);
  }
  return output_t;
}

// ---------------------------------------------------------------------
//
// Convolution backward (weight)
//
// ---------------------------------------------------------------------

void raw_cudnn_convolution_backward_weight_out(
    const Tensor& grad_weight, const Tensor& grad_output, const Tensor& input,
    IntList padding, IntList stride, IntList dilation, int64_t groups,
    bool benchmark, bool deterministic) {

  auto dataType = getCudnnDataType(input);

  ConvolutionArgs args{ input, grad_output, grad_weight };
  args.handle = getCudnnHandle();
  setConvolutionParams(&args.params, input, grad_weight, padding, stride, dilation, groups, deterministic);
  args.idesc.set(input);
  args.wdesc.set(grad_weight);
  args.odesc.set(grad_output);
  args.cdesc.set(dataType, input.dim() - 2, args.params.padding, args.params.stride, args.params.dilation, args.params.groups);

  cudnnConvolutionBwdFilterAlgo_t bwdFilterAlg;
  Workspace workspace = chooseAlgorithm(args, benchmark, &bwdFilterAlg);

  Constant one(dataType, 1);
  Constant zero(dataType, 0);

  CUDNN_CHECK(cudnnConvolutionBackwardFilter(
      args.handle,
      &one, args.idesc.desc(), input.data_ptr(),
      args.odesc.desc(), grad_output.data_ptr(),
      args.cdesc.desc(), bwdFilterAlg, workspace.data, workspace.size,
      &zero, args.wdesc.desc(), grad_weight.data_ptr()));
}

Tensor cudnn_convolution_backward_weight(
    CheckedFrom c,
    IntList weight_size, const TensorArg& grad_output, const TensorArg& input,
    IntList padding, IntList stride, IntList dilation, int64_t groups,
    bool benchmark, bool deterministic)
{

  checkAllSameType(c, {grad_output, input});
  checkAllSameGPU(c, {grad_output, input});

  auto grad_weight_t = grad_output->type().tensor(weight_size);

  // For uniformity with everything else, although it seems grad_weight
  // would be unambiguous too.
  TensorArg grad_weight{ grad_weight_t, "result", 0 };
  convolution_shape_check(c, input, grad_weight, grad_output, padding, stride, dilation, groups);

#if CUDNN_VERSION < 7000
  for (int i = 0; i < groups; i++) {
    raw_cudnn_convolution_backward_weight_out(
        narrowGroup(*grad_weight, weight_output_channels_dim, i, groups),
        narrowGroup(*grad_output, output_channels_dim, i, groups),
        narrowGroup(*input, input_channels_dim, i, groups),
        padding, stride, dilation, groups, benchmark, deterministic);
  }
#else
  raw_cudnn_convolution_backward_weight_out(
      *grad_weight, *grad_output, *input,
      padding, stride, dilation, groups, benchmark, deterministic);
#endif

  return grad_weight_t;
}

Tensor cudnn_convolution_backward_weight(
    IntList weight_size,
    const Tensor& grad_output_t,
    const Tensor& input_t,
    IntList padding, IntList stride, IntList dilation, int64_t groups,
    bool benchmark, bool deterministic)
{
  TensorArg grad_output{ grad_output_t, "grad_output", 1 },
            input{ input_t, "input", 2 };
  setCuDNNStreamToCurrent();
  return cudnn_convolution_backward_weight(
      "cudnn_convolution_backward_weight",
      weight_size, grad_output, input,
      padding, stride, dilation, groups, benchmark, deterministic);
}

Tensor cudnn_convolution_transpose_backward_weight(
    IntList weight_size,
    const Tensor& grad_output_t,
    const Tensor& input_t,
    IntList padding, IntList stride, IntList dilation, int64_t groups,
    bool benchmark, bool deterministic)
{
  TensorArg grad_output{ grad_output_t, "grad_output", 1 },
            input{ input_t, "input", 2 };
  setCuDNNStreamToCurrent();
  return cudnn_convolution_backward_weight(
      "cudnn_convolution_backward_weight",
      weight_size, input, grad_output,
      padding, stride, dilation, groups, benchmark, deterministic);
}

// ---------------------------------------------------------------------
//
// Convolution backward (bias)
//
// ---------------------------------------------------------------------

Tensor cudnn_convolution_backward_bias(
    const Tensor& grad_output_t)
{
  TensorArg grad_output{ grad_output_t, "grad_output", 1 };
  setCuDNNStreamToCurrent();

  auto grad_bias_t = grad_output->type().tensor(
                        { grad_output->size(output_channels_dim) });

  TensorArg grad_bias{ grad_bias_t, "result", 0 };

  // See Note [CuDNN broadcast padding].  Handle the left padding
  // ourselves, but use TensorDescriptor's pad argument to do the rest.
  TensorDescriptor bdesc{grad_bias->expand({1, grad_bias->size(0)}),
                         static_cast<size_t>(grad_output->dim())};
  TensorDescriptor odesc{*grad_output};

  auto handle = getCudnnHandle();
  auto dataType = getCudnnDataType(*grad_bias);
  Constant one(dataType, 1);
  Constant zero(dataType, 0);

  CUDNN_CHECK(cudnnConvolutionBackwardBias(handle, &one, odesc.desc(), grad_output->data_ptr(),
                                                   &zero, bdesc.desc(), grad_bias->data_ptr()));
  return *grad_bias;
}


}}  // namespace

#endif

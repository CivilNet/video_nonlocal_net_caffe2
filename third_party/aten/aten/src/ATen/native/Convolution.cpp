#include "ATen/ATen.h"
#include "ATen/NativeFunctions.h"

#include "ATen/Config.h"
#if AT_CUDNN_ENABLED()
#include "THC/THC.h"
#include "ATen/cudnn/cudnn-wrapper.h"
#endif

namespace at { namespace native {

struct ConvParams {
  std::vector<int64_t> stride;
  std::vector<int64_t> padding;
  std::vector<int64_t> dilation;
  bool transposed;
  std::vector<int64_t> output_padding;
  int groups;
  bool benchmark;
  bool deterministic;
  bool cudnn_enabled;

  bool is_strided() const;
  bool is_dilated() const;
  bool is_padded() const;
  bool is_output_padding_neg() const;
  bool is_output_padding_big() const;
  bool is_padding_neg() const;
  void view1d_as_2d();
  bool use_cudnn(const at::Tensor& input) const;
  bool use_mkldnn(const at::Tensor& input) const;
  bool is_depthwise(const at::Tensor& input, const at::Tensor& weight) const;
};

std::ostream& operator<<(std::ostream & out, const ConvParams& params) {
  out << "ConvParams {"
      << "  stride = " << IntList{params.stride}
      << "  padding = " << IntList{params.padding}
      << "  dilation = " << IntList{params.dilation}
      << "  transposed = " << params.transposed
      << "  output_padding = " << IntList{params.output_padding}
      << "  groups = " << params.groups
      << "  benchmark = " << params.benchmark
      << "  deterministic = " << params.deterministic
      << "  cudnn_enabled = " << params.cudnn_enabled
      << "}";
  return out;
}

auto ConvParams::is_strided() const -> bool {
  bool is_strided = false;
  for (int s : stride) {
    is_strided |= (s != 1);
  }
  return is_strided;
}

auto ConvParams::is_dilated() const -> bool {
  bool is_dilated = false;
  for (int d : dilation) {
    is_dilated |= (d != 1);
  }
  return is_dilated;
}

auto ConvParams::is_padded() const -> bool {
  bool is_padded = false;
  for (int p : padding) {
    is_padded |= (p != 0);
  }
  return is_padded;
}

auto ConvParams::is_output_padding_neg() const -> bool {
  bool is_non_neg = false;
  for (int p : output_padding) {
    is_non_neg |= (p < 0);
  }
  return is_non_neg;
}

auto ConvParams::is_output_padding_big() const -> bool {
  bool is_big = false;
  for (size_t i = 0; i < output_padding.size(); i++) {
    is_big |= (output_padding[i] >= stride[i] || output_padding[i] >= dilation[i]);
  }
  return is_big;
}

auto ConvParams::is_padding_neg() const -> bool {
  bool is_non_neg = false;
  for (int p : padding) {
    is_non_neg |= (p < 0);
  }
  return is_non_neg;
}


auto ConvParams::view1d_as_2d() -> void {
  if (stride.size() == 1) {
    stride.insert(stride.begin(), 1);
    padding.insert(padding.begin(), 0);
    dilation.insert(dilation.begin(), 1);
    output_padding.insert(output_padding.begin(), 0);
  }
}

auto ConvParams::use_cudnn(const at::Tensor& input) const -> bool {
#if AT_CUDNN_ENABLED()
  if (!input.type().is_cuda() || !cudnn_enabled) {
    return false;
  }
  if (deterministic && is_dilated()) {
    // cudnn doesn't support deterministic dilated convolution fully yet
    return false;
  }
  if (is_dilated()) {
    cudaDeviceProp* prop = THCState_getCurrentDeviceProperties(globalContext().thc_state);
    // NOTE: extra parenthesis around numbers disable clang warnings about dead code
    return ((CUDNN_VERSION >= (6021)) || (CUDNN_VERSION >= (6000) && prop->major >= 5)) && !is_output_padding_big();
  }
  return !is_output_padding_big();
#else
  (void)input; // avoid unused parameter warning
#endif
  return false;
}

auto ConvParams::use_mkldnn(const at::Tensor& input) const -> bool {
#if AT_MKLDNN_ENABLED()
  return input.type().backend() == kCPU &&
         input.type().scalarType() == kFloat && // only on CPU Float Tensors
         !is_dilated() && // doesn't support dilation
         !transposed && // or transposed tensors
         input.ndimension() == 4 && // must be in NCHW format
         groups == 1;
#endif
  return false;
}

// We currently only have depthwise support for the case where groups ==
// nInputPlane and nInputPlane == nOutputPlane (the latter due to the lack of
// a depthwise multiplier)
auto ConvParams::is_depthwise(
        const at::Tensor& input, const at::Tensor& weight) const -> bool {
  return input.type().is_cuda() &&
         !transposed &&
         input.ndimension() == 4 &&
         input.size(1) == groups &&
         groups > 1 && // no point if there is only a single group
         weight.size(0) % input.size(1) == 0; // output channels must be a multiple of input channels
}

static void check_input_shape_forward(const at::Tensor& input,
                                      const at::Tensor& weight, const at::Tensor& bias,
                                      int64_t groups, bool transposed) {
  int64_t k = input.ndimension();
  int64_t weight_dim = weight.ndimension();

  if (weight_dim != k) {
    std::stringstream ss;
    ss << "Expected " << k << "-dimensional weight for " << k
       << "-dimensional input " << input.sizes() << ", but got weight of size "
       << weight.sizes() << " instead";
    throw std::runtime_error(ss.str());
  }
  if (weight.size(0) < groups) {
    std::stringstream ss;
    ss << "Given groups=" << groups << ", expected weight to be at least "
       << groups << " at dimension 0, but got weight of size " << weight.sizes()
       << " instead";
    throw std::runtime_error(ss.str());
  }

  if (!transposed) {
    if (input.size(1) != (weight.size(1) * groups)) {
      std::stringstream ss;
      ss << "Given groups=" << groups << ", weight" << weight.sizes()
         << ", so expected input" << input.sizes() << " to have "
         << (weight.size(1) * groups) << " channels, but got " << input.size(1)
         << " channels instead";
      throw std::runtime_error(ss.str());
    }
    if (bias.defined() && (bias.ndimension() != 1 || bias.size(0) != weight.size(0))) {
      std::stringstream ss;
      ss << "Given weight of size " << weight.sizes()
         << ", expected bias to be 1-dimensional with " << weight.size(0) << " elements"
         << ", but got bias of size " << bias.sizes() << " instead";
      throw std::runtime_error(ss.str());
    }
  } else { // transposed
    if (input.size(1) != weight.size(0)) {
      std::stringstream ss;
      ss << "Given transposed=" << transposed << ", weight" << weight.sizes()
         << ", so expected input" << input.sizes() << " to have "
         << weight.size(0) << " channels, but got " << input.size(1)
         << " channels instead";
      throw std::runtime_error(ss.str());
    }
    if (bias.defined() && (bias.ndimension() != 1 || bias.size(0) != weight.size(1) * groups)) {
      std::stringstream ss;
      ss << "Given transposed=" << transposed << ", weight of size " << weight.sizes()
         << ", expected bias to be 1-dimensional with " << weight.size(1) * groups << " elements"
         << ", but got bias of size " << bias.sizes() << " instead";
      throw std::runtime_error(ss.str());
    }
  }
}

static auto view4d(const at::Tensor& tensor) -> at::Tensor {
  if (tensor.ndimension() != 3) throw std::runtime_error("expected 3D tensor");
  return tensor.unsqueeze(2);
}

static auto view3d(const at::Tensor& tensor) -> at::Tensor {
  if (tensor.ndimension() != 4) throw std::runtime_error("expected 4D tensor");
  return tensor.squeeze(2);
}


static at::Tensor subtensor(at::Tensor& tensor, int dim, int groups, int g) {
  if (!tensor.defined()) {
    return at::Tensor();
  }
  int64_t n = tensor.sizes()[dim] / groups;
  return tensor.narrow(dim, n * g, n).contiguous();
}


at::Tensor conv1d(
    const Tensor& input, const Tensor& weight, const Tensor& bias,
    IntList stride, IntList padding, IntList dilation, int64_t groups) {
  return at::convolution(input, weight, bias, stride, padding, dilation,
                         false, {{0}}, groups);
}

at::Tensor conv2d(
    const Tensor& input, const Tensor& weight, const Tensor& bias,
    IntList stride, IntList padding, IntList dilation, int64_t groups) {
  return at::convolution(input, weight, bias, stride, padding, dilation,
                         false, {{0, 0}}, groups);
}

at::Tensor conv3d(
    const Tensor& input, const Tensor& weight, const Tensor& bias,
    IntList stride, IntList padding, IntList dilation, int64_t groups) {
  return at::convolution(input, weight, bias, stride, padding, dilation,
                         false, {{0, 0, 0}}, groups);
}

at::Tensor conv_transpose1d(
    const Tensor& input, const Tensor& weight, const Tensor& bias,
    IntList stride, IntList padding, IntList output_padding, int64_t groups, IntList dilation) {
  return at::convolution(input, weight, bias, stride, padding, dilation,
                         true, output_padding, groups);
}

at::Tensor conv_transpose2d(
    const Tensor& input, const Tensor& weight, const Tensor& bias,
    IntList stride, IntList padding, IntList output_padding, int64_t groups, IntList dilation) {
  return at::convolution(input, weight, bias, stride, padding, dilation,
                         true, output_padding, groups);
}

at::Tensor conv_transpose3d(
    const Tensor& input, const Tensor& weight, const Tensor& bias,
    IntList stride, IntList padding, IntList output_padding, int64_t groups, IntList dilation) {
  return at::convolution(input, weight, bias, stride, padding, dilation,
                         true, output_padding, groups);
}

at::Tensor convolution(
    const Tensor& input, const Tensor& weight, const Tensor& bias,
    IntList stride, IntList padding, IntList dilation,
    bool transposed, IntList output_padding, int64_t groups) {
  auto& ctx = at::globalContext();
  return at::_convolution(input, weight, bias, stride, padding, dilation,
                          transposed, output_padding, groups,
                          ctx.benchmarkCuDNN(), ctx.deterministicCuDNN(), ctx.userEnabledCuDNN());
}

static inline std::vector<int64_t> convolution_expand_param_if_needed(
  IntList list_param, const char *param_name, int64_t expected_dim) {
  if (list_param.size() == 1) {
    return std::vector<int64_t>(expected_dim, list_param[0]);
  } else if ((int64_t) list_param.size() != expected_dim) {
    std::ostringstream ss;
    ss << "expected " << param_name << " to be a single integer value or a "
       << "list of " << expected_dim << " values to match the convolution "
       << "dimensions, but got " << param_name << "=" << list_param;
    throw std::runtime_error(ss.str());
  } else {
    return list_param.vec();
  }
}

at::Tensor _convolution(
    const Tensor& input_r, const Tensor& weight_r, const Tensor& bias_r,
    IntList stride_, IntList padding_, IntList dilation_,
    bool transposed_, IntList output_padding_, int64_t groups_,
    bool benchmark, bool deterministic, bool cudnn_enabled) {

  auto input = input_r.contiguous();
  auto weight = weight_r;
  auto bias = bias_r;
  auto k = input.ndimension();
  int64_t dim = k - 2;

  if (dim <= 0) {
    throw std::runtime_error("input has less dimensions than expected");
  }

  ConvParams params;
  params.stride = convolution_expand_param_if_needed(stride_, "stride", dim);
  params.padding = convolution_expand_param_if_needed(padding_, "padding", dim);
  params.dilation = convolution_expand_param_if_needed(dilation_, "dilation", dim);
  params.transposed = transposed_;
  params.output_padding = convolution_expand_param_if_needed(output_padding_, "output_padding", dim);
  params.groups = groups_;
  params.benchmark = benchmark;
  params.deterministic = deterministic;
  params.cudnn_enabled = cudnn_enabled;

  if (params.is_padding_neg()) throw std::runtime_error("negative padding is not supported");
  if (params.is_output_padding_neg()) throw std::runtime_error("negative output_padding is not supported");

  check_input_shape_forward(input, weight, bias, params.groups, params.transposed);

  if (k == 3) {
    params.view1d_as_2d();
    input = view4d(input);
    weight = view4d(weight);
  }

  auto output = input.type().tensor();

  if (params.is_depthwise(input, weight)) {
      /* output.resize_(output_size(input, weight)); */

      auto kernel_size = weight.sizes().slice(2);
      auto stride = params.stride;
      auto padding = params.padding;
      auto dilation = params.dilation;

      output = at::thnn_conv_depthwise2d(input, weight, kernel_size, bias, stride, padding, dilation);
  } else if (params.use_cudnn(input)) {
#if AT_CUDNN_ENABLED()
    if (input.type() != weight.type()){
      std::stringstream ss;
      ss << "Input type (" << input.type().toString() << ") and weight type (" << weight.type().toString() << ") should be the same";
      throw std::runtime_error(ss.str());
    }
    if (bias.defined() && input.type() != bias.type()){
      std::stringstream ss;
      ss << "Input type (" << input.type().toString() << ") and bias type (" << bias.type().toString() << ") should be the same";
      throw std::runtime_error(ss.str());
    }

    if (params.transposed) {
      output = at::cudnn_convolution_transpose(
          input, weight, bias,
          params.padding, params.output_padding, params.stride, params.dilation, params.groups, params.benchmark, params.deterministic);
    } else {
      output = at::cudnn_convolution(
          input, weight, bias,
          params.padding, params.stride, params.dilation, params.groups, params.benchmark, params.deterministic);
    }
#endif
  } else if (params.use_mkldnn(input)) {
#if AT_MKLDNN_ENABLED()
    if (input.type() != weight.type()){
      std::stringstream ss;
      ss << "Input type (" << input.toString() << ") and weight type (" << weight.toString() << ") should be the same";
      throw std::runtime_error(ss.str());
    }
    if (bias.defined() && input.type() != bias.type()){
      std::stringstream ss;
      ss << "Input type (" << input.toString() << ") and bias type (" << bias.toString() << ") should be the same";
      throw std::runtime_error(ss.str());
    }

    output = at::mkldnn_convolution(input, weight, bias, params.padding, params.stride, params.dilation);
#endif
  } else {
    if (params.groups == 1) {
      output = at::_convolution_nogroup(
          input, weight, bias, params.stride, params.padding, params.dilation, params.transposed, params.output_padding);
    } else {
      std::vector<Tensor> outputs(params.groups);
      for (int g = 0; g < params.groups; ++g) {
        auto input_g = subtensor(input, 1, params.groups, g);
        auto weight_g = subtensor(weight, 0, params.groups, g);
        auto bias_g = subtensor(bias, 0, params.groups, g);
        outputs[g] = at::_convolution_nogroup(
            input_g, weight_g, bias_g, params.stride, params.padding, params.dilation, params.transposed, params.output_padding);
      }
      output = at::cat(outputs, 1);
    }
  }

  if (k == 3) {
    output = view3d(output);
  }

  return output;
}

// A generic function for convolution implementations which don't
// natively implement groups (e.g., not CuDNN).
at::Tensor _convolution_nogroup(
    const Tensor& input, const Tensor& weight, const Tensor& bias,
    IntList stride, IntList padding, IntList dilation,
    bool transposed, IntList output_padding) {

  ConvParams params;
  params.stride = stride;
  params.padding = padding;
  params.dilation = dilation;
  params.transposed = transposed;
  params.output_padding = output_padding;
  params.groups = 1;
  params.benchmark = false;
  params.deterministic = false;
  params.cudnn_enabled = false;

  auto dim = input.ndimension();
  auto dilated = params.is_dilated();
  auto kernel_size = weight.sizes().slice(2);

  if (params.transposed) {
    if (dim == 4) {
      return at::thnn_conv_transpose2d(
          input, weight, kernel_size, bias,
          stride, padding, output_padding, dilation);
    } else if (dim == 5) {
      return at::thnn_conv_transpose3d(
        input, weight, kernel_size, bias,
        stride, padding, output_padding, dilation);
      }
  } else {  /* Not transposed */
    if (dim == 4) {
      if (dilated) {
        return at::thnn_conv_dilated2d(
            input, weight, kernel_size, bias,
            stride, padding, dilation);
      } else {  /* dim == 4, non-dilated */
        /* CPU implementation has specialized MM kernels
           for non-dilated case here */
        return at::thnn_conv2d(
            input, weight, kernel_size, bias,
            stride, padding);
      }
    } else if (dim == 5 && (input.type().is_cuda() || dilated)) {
      return at::thnn_conv_dilated3d(
          input, weight, kernel_size, bias,
          stride, padding, dilation);
    } else if (dim == 5) { /* dim == 5, CPU, non-dilated */
      /* CPU implementation has specialized MM kernels
         for non-dilated case here */
      return at::thnn_conv3d(
          input, weight, kernel_size, bias,
          stride, padding);
    }
  }

  throw std::runtime_error("unsupported ConvNd parameters");
}

static Tensor subvariable(const Tensor& var, int dim, int groups, int g) {
  int64_t n = var.sizes()[dim] / groups;
  auto result = var.narrow(dim, n * g, n);
  return result;
}

std::tuple<Tensor,Tensor,Tensor> _convolution_double_backward(
    const Tensor& ggI, const Tensor& ggW_r, const Tensor& ggb,
    const Tensor& gO_r, const Tensor& weight_r, const Tensor& input,
    IntList stride_, IntList padding_, IntList dilation_,
    bool transposed_, IntList output_padding_, int64_t groups_,
    bool benchmark, bool deterministic, bool cudnn_enabled,
    std::array<bool, 3> output_mask) {

  auto ggW = ggW_r;
  auto gO = gO_r;
  auto weight = weight_r;

  ConvParams params;
  params.stride = stride_;
  params.padding = padding_;
  params.dilation = dilation_;
  params.transposed = transposed_;
  params.output_padding = output_padding_;
  params.groups = groups_;
  params.benchmark = benchmark;
  params.deterministic = deterministic;
  params.cudnn_enabled = cudnn_enabled;

  // Compute ggO = conv(ggI, w) + conv(i, ggW) + ggb
  Tensor ggO;
  if (ggI.defined()) {
    if (weight.type().is_cuda()) {
      weight = weight.contiguous();
    }
    ggO = at::_convolution(ggI, weight, Tensor(), params.stride, params.padding, params.dilation, params.transposed, params.output_padding, params.groups, params.benchmark, params.deterministic, params.cudnn_enabled);
  }

  if (ggW.defined()) {
    if (ggW.type().is_cuda()) {
      ggW = ggW.contiguous();
    }
    auto ggW_term = at::_convolution(input, ggW, Tensor(), params.stride, params.padding, params.dilation, params.transposed, params.output_padding, params.groups, params.benchmark, params.deterministic, params.cudnn_enabled);
    if (ggO.defined()) {
      ggO = ggO + ggW_term;
    } else {
      ggO = ggW_term;
    }
  }

  if (ggb.defined()) {
    // View as (1, ggb.size(0), 1, 1...)

    // Expand
    std::vector<int64_t> new_size(gO.ndimension(), 1);
    new_size[1] = ggb.sizes()[0];
    auto ggb_contiguous = ggb.contiguous();
    auto ggb_view = ggb_contiguous.view(new_size);

    // Expand
    auto ggb_expanded = ggb_view.expand(gO.sizes());

    if (ggO.defined()) {
      ggO = ggO + ggb_expanded;
    } else {
      ggO = ggb_expanded;
    }
  }

  // Compute gW = conv(ggI, gO)
  Tensor gW;
  if (ggI.defined()) {
    // Modified params with correct padding
    ConvParams gw_conv_params(params);

    // Disable groups as they are handled separately
    auto groups = gw_conv_params.groups;
    gw_conv_params.groups = 1;
    std::swap(gw_conv_params.dilation, gw_conv_params.stride);

    // Transpose gO and ggI to accumulate over batch
    auto gOt = gO.transpose(0, 1);
    auto ggIt = ggI.transpose(0, 1);

    Tensor gWt;
    // Compute conv
    if (groups == 1) {
      if (gOt.type().is_cuda()) {
        gOt = gOt.contiguous();
      }

      // Compute conv
      if (params.transposed) {
        gw_conv_params.transposed = false;
        gWt = at::_convolution(gOt, ggIt, Tensor(), gw_conv_params.stride, gw_conv_params.padding, gw_conv_params.dilation, gw_conv_params.transposed, gw_conv_params.output_padding, gw_conv_params.groups, gw_conv_params.benchmark, gw_conv_params.deterministic, gw_conv_params.cudnn_enabled);
      } else {
        gWt = at::_convolution(ggIt, gOt, Tensor(), gw_conv_params.stride, gw_conv_params.padding, gw_conv_params.dilation, gw_conv_params.transposed, gw_conv_params.output_padding, gw_conv_params.groups, gw_conv_params.benchmark, gw_conv_params.deterministic, gw_conv_params.cudnn_enabled);
      }
    } else {
      std::vector<Tensor> gWt_list(groups);
      for (int g = 0; g < groups; ++g) {
        auto ggIt_g = subvariable(ggIt, 0, groups, g);
        auto gOt_g = subvariable(gOt, 0, groups, g);
        if (gOt_g.type().is_cuda()) {
          gOt_g = gOt_g.contiguous();
        }

        // Compute conv
        if (params.transposed) {
          gw_conv_params.transposed = false;
          gWt_list[g] = at::_convolution(gOt_g, ggIt_g, Tensor(), gw_conv_params.stride, gw_conv_params.padding, gw_conv_params.dilation, gw_conv_params.transposed, gw_conv_params.output_padding, gw_conv_params.groups, gw_conv_params.benchmark, gw_conv_params.deterministic, gw_conv_params.cudnn_enabled);
        } else {
          gWt_list[g] = at::_convolution(ggIt_g, gOt_g, Tensor(), gw_conv_params.stride, gw_conv_params.padding, gw_conv_params.dilation, gw_conv_params.transposed, gw_conv_params.output_padding, gw_conv_params.groups, gw_conv_params.benchmark, gw_conv_params.deterministic, gw_conv_params.cudnn_enabled);
        }
      }

      gWt = at::cat(gWt_list, 1);
    }

    // Transpose gW to match chan_in and chan_out
    gW = gWt.transpose(0, 1);

    // narrow gW to only relevant portion
    // we do it this way instead of narrowing the input itself because
    // the ConvForward kernels don't support asymmetric padding.
    auto gW_size = gW.sizes();
    auto w_size = weight.sizes();
    for (size_t i = 2; i < gW_size.size(); ++i) {
      if (gW_size[i] > w_size[i]) {
          gW = gW.narrow(i, 0, w_size[i]);
          gW_size = gW.sizes();
      }
    }
  }

  // Compute gI = convT(ggW, gO.t()) if !transposed
  //         gI = conv(go, ggw)      if transposed
  Tensor gI;
  if (ggW.defined()) {
    ConvParams gi_conv_params(params);
    gi_conv_params.transposed = !params.transposed;

    if (params.transposed) {
      if (gO.type().is_cuda()) {
        gO = gO.contiguous();
      }
      gI = at::_convolution(gO, ggW, Tensor(), gi_conv_params.stride, gi_conv_params.padding, gi_conv_params.dilation, gi_conv_params.transposed, gi_conv_params.output_padding, gi_conv_params.groups, gi_conv_params.benchmark, gi_conv_params.deterministic, gi_conv_params.cudnn_enabled);

      // narrow gI to only relevant portion
      // we do it this way because negative output_padding is not supported
      // TODO: figure out if we can narrow gO and save some compute,
      // rather than narrowing the computed gI
      auto gI_size = gI.sizes();
      auto i_size = input.sizes();
      for (size_t i = 2; i < gI_size.size(); ++i) {
        if (gI_size[i] > i_size[i]) {
          gI = gI.narrow(i, 0, i_size[i]);
          gI_size = gI.sizes();
        }
      }
    } else {
      auto groups = gi_conv_params.groups;
      gi_conv_params.groups = 1;
      // swap stride and dilation
      std::swap(gi_conv_params.dilation, gi_conv_params.stride);

      auto ggWt = ggW.transpose(0, 1);
      auto gOt = gO.transpose(0, 1);

      // calculate output_padding
      // TODO: figure out why this needs to be computed...
      auto kernel_size = weight.sizes().slice(2);
      auto input_shape = input.sizes().slice(2);
      auto grad_output_shape = gO.sizes().slice(2);

      if (kernel_size.size() == 1) {
        auto expected_input_shape = (kernel_size[0] - 1) * gi_conv_params.stride[1]
          - 2 * gi_conv_params.padding[1]
          + (gi_conv_params.dilation[1] * (grad_output_shape[0] - 1) + 1);
        if (expected_input_shape != input_shape[0]) {
          gi_conv_params.output_padding[1] = input_shape[0] - expected_input_shape;
        }
      } else {
        for(size_t i = 0; i < kernel_size.size(); ++i) {
          // Check if whole input has been used or not
          auto expected_input_shape = (kernel_size[i] - 1) * gi_conv_params.stride[i]
            - 2 * gi_conv_params.padding[i]
            + (gi_conv_params.dilation[i] * (grad_output_shape[i] - 1) + 1);
          if (expected_input_shape != input_shape[i]) {
            gi_conv_params.output_padding[i] = input_shape[i] - expected_input_shape;
          }
        }
      }

      Tensor gIt;
      if (params.groups == 1) {
        if (gOt.type().is_cuda()) {
          gOt = gOt.contiguous();
        }

        gIt = at::_convolution(ggWt, gOt, Tensor(), gi_conv_params.stride, gi_conv_params.padding, gi_conv_params.dilation, gi_conv_params.transposed, gi_conv_params.output_padding, gi_conv_params.groups, gi_conv_params.benchmark, gi_conv_params.deterministic, gi_conv_params.cudnn_enabled);
      } else {
        std::vector<Tensor> gIt_list(params.groups);
        for (int g = 0; g < groups; ++g) {
          auto ggWt_g = subvariable(ggWt, 1, groups, g);
          auto gOt_g = subvariable(gOt, 0, groups, g);
          if (gOt_g.type().is_cuda()) {
            gOt_g = gOt_g.contiguous();
          }

          gIt_list[g] = at::_convolution(ggWt_g, gOt_g, Tensor(), gi_conv_params.stride, gi_conv_params.padding, gi_conv_params.dilation, gi_conv_params.transposed, gi_conv_params.output_padding, gi_conv_params.groups, gi_conv_params.benchmark, gi_conv_params.deterministic, gi_conv_params.cudnn_enabled);
        }

        gIt = at::cat(gIt_list, 0);
      }

      gI = gIt.transpose(0, 1);
    }
  }

  if (output_mask[0] && !ggO.defined()) ggO = at::zeros_like(gO);
  if (output_mask[1] && !gI.defined()) gI = at::zeros_like(input);
  if (output_mask[2] && !gW.defined()) gW = at::zeros_like(weight);

  return std::tuple<Tensor,Tensor,Tensor>{ggO, gI, gW};
}

}} // at::native

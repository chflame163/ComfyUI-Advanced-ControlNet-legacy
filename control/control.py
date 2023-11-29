from typing import Union
from torch import Tensor
import torch

import comfy.utils
import comfy.controlnet as comfy_cn
from comfy.controlnet import ControlBase, ControlNet, ControlLora, T2IAdapter, broadcast_image_to


def get_properly_arranged_t2i_weights(initial_weights: list[float]):
    new_weights = []
    new_weights.extend([initial_weights[0]]*3)
    new_weights.extend([initial_weights[1]]*3)
    new_weights.extend([initial_weights[2]]*3)
    new_weights.extend([initial_weights[3]]*3)
    return new_weights


class ControlWeightType:
    DEFAULT = "default"
    UNIVERSAL = "universal"
    T2IADAPTER = "t2iadapter"
    CONTROLNET = "controlnet"
    CONTROLLORA = "controllora"
    CONTROLLLLITE = "controllllite"


class ControlWeights:
    def __init__(self, weight_type: str, base_multiplier: float=1.0, flip_weights: bool=False, weights: list[float]=None, weight_mask: Tensor=None):
        self.weight_type = weight_type
        self.base_multiplier = base_multiplier
        self.flip_weights = flip_weights
        self.weights = weights
        if self.weights is not None and self.flip_weights:
            self.weights.reverse()
        self.weight_mask = weight_mask

    def get(self, idx: int, shape: Tensor) -> Union[float, Tensor]:
        # if weight_mask present, return normalized mask
        if self.base_multiplier != 1.0 and self.weight_mask is not None:
            # TODO: fill out
            pass 
        # if weights is not none, return index
        if self.weights is not None:
            return self.weights[idx]
        return 1.0

    @classmethod
    def default(cls):
        return cls(ControlWeightType.DEFAULT)

    @classmethod
    def universal(cls, base_multiplier: float, flip_weights: bool=False):
        return cls(ControlWeightType.UNIVERSAL, base_multiplier=base_multiplier, flip_weights=flip_weights)
    
    @classmethod
    def t2iadapter(cls, weights: list[float]=None, flip_weights: bool=False):
        if weights is None:
            weights = [1.0]*12
        return cls(ControlWeightType.T2IADAPTER, weights=weights,flip_weights=flip_weights)

    @classmethod
    def controlnet(cls, weights: list[float]=None, flip_weights: bool=False):
        if weights is None:
            weights = [1.0]*13
        return cls(ControlWeightType.CONTROLNET, weights=weights, flip_weights=flip_weights)
    
    @classmethod
    def controllora(cls, weights: list[float]=None, flip_weights: bool=False):
        if weights is None:
            weights = [1.0]*10
        return cls(ControlWeightType.CONTROLLORA, weights=weights, flip_weights=flip_weights)
    
    @classmethod
    def controllllite(cls, weights: list[float]=None, flip_weights: bool=False):
        if weights is None:
            # TODO: make this have a real value
            weights = [1.0]*200
        return cls(ControlWeightType.CONTROLLLLITE, weights=weights, flip_weights=flip_weights)


class StrengthInterpolation:
    LINEAR = "linear"
    EASE_IN = "ease-in"
    EASE_OUT = "ease-out"
    EASE_IN_OUT = "ease-in-out"
    NONE = "none"


class LatentKeyframe:
    def __init__(self, batch_index: int, strength: float) -> None:
        self.batch_index = batch_index
        self.strength = strength


# always maintain sorted state (by batch_index of LatentKeyframe)
class LatentKeyframeGroup:
    def __init__(self) -> None:
        self.keyframes: list[LatentKeyframe] = []

    def add(self, keyframe: LatentKeyframe) -> None:
        added = False
        # replace existing keyframe if same batch_index
        for i in range(len(self.keyframes)):
            if self.keyframes[i].batch_index == keyframe.batch_index:
                self.keyframes[i] = keyframe
                added = True
                break
        if not added:
            self.keyframes.append(keyframe)
        self.keyframes.sort(key=lambda k: k.batch_index)
    
    def get_index(self, index: int) -> Union[LatentKeyframe, None]:
        try:
            return self.keyframes[index]
        except IndexError:
            return None
    
    def __getitem__(self, index) -> LatentKeyframe:
        return self.keyframes[index]
    
    def is_empty(self) -> bool:
        return len(self.keyframes) == 0

    def clone(self) -> 'LatentKeyframeGroup':
        cloned = LatentKeyframeGroup()
        for tk in self.keyframes:
            cloned.add(tk)
        return cloned


class TimestepKeyframe:
    def __init__(self,
                 start_percent: float = 0.0,
                 strength: float = 1.0,
                 interpolation: str = StrengthInterpolation.NONE,
                 control_weights: ControlWeights = None,
                 latent_keyframes: LatentKeyframeGroup = None,
                 null_latent_kf_strength: float = 0.0,
                 inherit_missing: bool = True,
                 guarantee_usage: bool = True,
                 mask_hint_orig: Tensor = None) -> None:
        self.start_percent = start_percent
        self.start_t = 999999999.9
        self.strength = strength
        self.interpolation = interpolation
        self.control_weights = control_weights
        self.latent_keyframes = latent_keyframes
        self.null_latent_kf_strength = null_latent_kf_strength
        self.inherit_missing = inherit_missing
        self.guarantee_usage = guarantee_usage
        self.mask_hint_orig = mask_hint_orig

    def has_control_weights(self):
        return self.control_weights is not None
    
    def has_latent_keyframes(self):
        return self.latent_keyframes is not None
    
    def has_mask_hint(self):
        return self.mask_hint_orig is not None
    
    
    @classmethod
    def default(cls) -> 'TimestepKeyframe':
        return cls(0.0)


# always maintain sorted state (by start_percent of TimestepKeyFrame)
class TimestepKeyframeGroup:
    def __init__(self) -> None:
        self.keyframes: list[TimestepKeyframe] = []
        self.keyframes.append(TimestepKeyframe.default())

    def add(self, keyframe: TimestepKeyframe) -> None:
        added = False
        # replace existing keyframe if same start_percent
        for i in range(len(self.keyframes)):
            if self.keyframes[i].start_percent == keyframe.start_percent:
                self.keyframes[i] = keyframe
                added = True
                break
        if not added:
            self.keyframes.append(keyframe)
        self.keyframes.sort(key=lambda k: k.start_percent)

    def get_index(self, index: int) -> Union[TimestepKeyframe, None]:
        try:
            return self.keyframes[index]
        except IndexError:
            return None
    
    def has_index(self, index: int) -> int:
        return index >=0 and index < len(self.keyframes)

    def __getitem__(self, index) -> TimestepKeyframe:
        return self.keyframes[index]
    
    def __len__(self) -> int:
        return len(self.keyframes)

    def is_empty(self) -> bool:
        return len(self.keyframes) == 0
    
    def clone(self) -> 'TimestepKeyframeGroup':
        cloned = TimestepKeyframeGroup()
        for tk in self.keyframes:
            cloned.add(tk)
        return cloned
    
    @classmethod
    def default(cls, keyframe: TimestepKeyframe) -> 'TimestepKeyframeGroup':
        group = cls()
        group.keyframes[0] = keyframe
        return group


# used to inject ControlNetAdvanced and T2IAdapterAdvanced control_merge function


class AdvancedControlBase:
    def __init__(self, base: ControlBase, timestep_keyframes: TimestepKeyframeGroup, weights_default: ControlWeights):
        self.base = base
        self.compatible_weights = [ControlWeightType.UNIVERSAL]
        self.add_compatible_weight(weights_default.weight_type)
        # mask for which parts of controlnet output to keep
        self.mask_cond_hint_original = None
        self.mask_cond_hint = None
        self.tk_mask_cond_hint_original = None
        self.tk_mask_cond_hint = None
        self.weight_mask_cond_hint = None
        # actual index values
        self.sub_idxs = None
        self.full_latent_length = 0
        self.context_length = 0
        # timesteps
        self.t: Tensor = None
        self.batched_number: int = None
        # weights + override
        self.weights: ControlWeights = None
        self.weights_default: ControlWeights = weights_default
        self.weights_override: ControlWeights = None
        # latent keyframe + override
        self.latent_keyframes: LatentKeyframeGroup = None
        self.latent_keyframe_override: LatentKeyframeGroup = None
        # initialize timestep_keyframes
        self.set_timestep_keyframes(timestep_keyframes)
        # override some functions
        self.get_control = self.get_control_inject
        self.control_merge = self.control_merge_inject#.__get__(self, type(self))
        self.pre_run = self.pre_run_inject
        self.cleanup = self.cleanup_inject

    def add_compatible_weight(self, control_weight_type: str):
        self.compatible_weights.append(control_weight_type)

    def set_timestep_keyframes(self, timestep_keyframes: TimestepKeyframeGroup):
        self.timestep_keyframes = timestep_keyframes if timestep_keyframes else TimestepKeyframeGroup()
        # prepare first timestep_keyframe related stuff
        self.current_timestep_keyframe = None
        self.current_timestep_index = -1
        self.next_timestep_keyframe = None
        self.weights = None
        self.latent_keyframes = None

    def prepare_current_timestep(self, t: Tensor, batched_number: int):
        self.t = t
        self.batched_number = batched_number
        # get current step percent
        curr_t: float = t[0]
        print(f"$$$$ {curr_t}")
        prev_index = self.current_timestep_index
        # if has next index, loop through and see if need to switch
        if self.timestep_keyframes.has_index(self.current_timestep_index+1):
            for i in range(self.current_timestep_index+1, len(self.timestep_keyframes)):
                eval_tk = self.timestep_keyframes[i]
                # check if start percent is less or equal to curr_t
                if eval_tk.start_t >= curr_t:
                    self.current_timestep_index = i
                    self.current_timestep_keyframe = eval_tk
                    # keep track of weights and control weights, accounting for inherit_missing
                    if self.current_timestep_keyframe.has_control_weights():
                        self.weights = self.current_timestep_keyframe.control_weights
                    elif not self.current_timestep_keyframe.inherit_missing:
                        self.weights = self.weights_default
                    if self.current_timestep_keyframe.has_latent_keyframes():
                        self.latent_keyframes = self.current_timestep_keyframe.latent_keyframes
                    elif not self.current_timestep_keyframe.inherit_missing:
                        self.latent_keyframes = None
                    if self.current_timestep_keyframe.has_mask_hint():
                        self.tk_mask_cond_hint_original = self.current_timestep_keyframe.mask_hint_orig
                    elif not self.current_timestep_keyframe.inherit_missing:
                        del self.tk_mask_cond_hint_original
                        self.tk_mask_cond_hint_original = None
                    # if guarantee_usage, stop searching for other TKs
                    if self.current_timestep_keyframe.guarantee_usage:
                        break
                # if eval_tk is outside of percent range, stop looking further
                else:
                    break
        
        # if index changed, apply overrides
        if prev_index != self.current_timestep_index:
            if self.weights_override is not None:
                self.weights = self.weights_override
            if self.latent_keyframe_override is not None:
                self.latent_keyframes = self.latent_keyframe_override

        # make sure weights and latent_keyframes are in a workable state
        # Note: each AdvancedControlBase should create their own get_universal_weights class
        self.prepare_weights()
    
    def prepare_weights(self):
        if self.weights is None or self.weights.weight_type == ControlWeightType.DEFAULT:
            self.weights = self.weights_default
        elif self.weights.weight_type == ControlWeightType.UNIVERSAL:
            self.weights = self.get_universal_weights()
    
    def get_universal_weights(self) -> ControlWeights:
        return self.weights

    def set_cond_hint_mask(self, mask_hint):
        self.mask_cond_hint_original = mask_hint
        return self

    def pre_run_inject(self, model, percent_to_timestep_function):
        self.base.pre_run(model, percent_to_timestep_function)
        self.pre_run_advanced(model, percent_to_timestep_function)
    
    def pre_run_advanced(self, model, percent_to_timestep_function):
        # for each timestep keyframe, calculate the start_t
        for tk in self.timestep_keyframes.keyframes:
            tk.start_t = percent_to_timestep_function(tk.start_percent)
        # clear variables
        self.cleanup_advanced()

    def get_control_inject(self, x_noisy, t, cond, batched_number):
        # prepare timestep and everything related
        self.prepare_current_timestep(t=t, batched_number=batched_number)
        # if should not perform any actions for the controlnet, exit without doing any work
        if self.strength == 0.0 or self.current_timestep_keyframe.strength == 0.0:
            control_prev = None
            if self.previous_controlnet is not None:
                control_prev = self.previous_controlnet.get_control(x_noisy, t, cond, batched_number)
            if control_prev is not None:
                return control_prev
            else:
                return None
        # otherwise, perform normal function
        return self.get_control_advanced(x_noisy, t, cond, batched_number)

    def get_control_advanced(self, x_noisy, t, cond, batched_number):
        pass

    def apply_advanced_strengths_and_masks(self, x: Tensor, batched_number: int):
        # apply strengths, and get batch indeces to null out
        # AKA latents that should not be influenced by ControlNet
        if self.latent_keyframes is not None:
            latent_count = x.size(0)//batched_number
            indeces_to_null = set(range(latent_count))
            mapped_indeces = None
            # if expecting subdivision, will need to translate between subset and actual idx values
            if self.sub_idxs:
                mapped_indeces = {}
                for i, actual in enumerate(self.sub_idxs):
                    mapped_indeces[actual] = i
            for keyframe in self.latent_keyframes:
                real_index = keyframe.batch_index
                # if negative, count from end
                if real_index < 0:
                    real_index += latent_count if self.sub_idxs is None else self.full_latent_length

                # if not mapping indeces, what you see is what you get
                if mapped_indeces is None:
                    if real_index in indeces_to_null:
                        indeces_to_null.remove(real_index)
                # otherwise, see if batch_index is even included in this set of latents
                else:
                    real_index = mapped_indeces.get(real_index, None)
                    if real_index is None:
                        continue
                    indeces_to_null.remove(real_index)

                # apply strength for each batched cond/uncond
                for b in range(batched_number):
                    x[(latent_count*b)+real_index] = x[(latent_count*b)+real_index] * keyframe.strength

            # null them out by multiplying by null_latent_kf_strength
            for batch_index in indeces_to_null:
                # apply null for each batched cond/uncond
                for b in range(batched_number):
                    x[(latent_count*b)+batch_index] = x[(latent_count*b)+batch_index] * self.current_timestep_keyframe.null_latent_kf_strength
        # apply masks, resizing mask to required dims
        if self.mask_cond_hint is not None:
            masks = prepare_mask_batch(self.mask_cond_hint, x.shape)
            x[:] = x[:] * masks
        if self.tk_mask_cond_hint is not None:
            masks = prepare_mask_batch(self.tk_mask_cond_hint, x.shape)
            x[:] = x[:] * masks
        # apply timestep keyframe strengths
        if self.current_timestep_keyframe.strength != 1.0:
            x[:] *= self.current_timestep_keyframe.strength
    
    def control_merge_inject(self: 'AdvancedControlBase', control_input, control_output, control_prev, output_dtype):
        out = {'input':[], 'middle':[], 'output': []}

        if control_input is not None:
            for i in range(len(control_input)):
                key = 'input'
                x = control_input[i]
                if x is not None:
                    self.apply_advanced_strengths_and_masks(x, self.batched_number)

                    x *= self.strength * self.weights.get(i, x.shape)
                    if x.dtype != output_dtype:
                        x = x.to(output_dtype)
                out[key].insert(0, x)

        if control_output is not None:
            for i in range(len(control_output)):
                if i == (len(control_output) - 1):
                    key = 'middle'
                    index = 0
                else:
                    key = 'output'
                    index = i
                x = control_output[i]
                if x is not None:
                    self.apply_advanced_strengths_and_masks(x, self.batched_number)

                    if self.global_average_pooling:
                        x = torch.mean(x, dim=(2, 3), keepdim=True).repeat(1, 1, x.shape[2], x.shape[3])

                    x *= self.strength * self.weights.get(i, x.shape)
                    if x.dtype != output_dtype:
                        x = x.to(output_dtype)

                out[key].append(x)
        if control_prev is not None:
            for x in ['input', 'middle', 'output']:
                o = out[x]
                for i in range(len(control_prev[x])):
                    prev_val = control_prev[x][i]
                    if i >= len(o):
                        o.append(prev_val)
                    elif prev_val is not None:
                        if o[i] is None:
                            o[i] = prev_val
                        else:
                            o[i] += prev_val
        return out

    def prepare_mask_cond_hint(self, x_noisy: Tensor, t, cond, batched_number, dtype=None):
        # make mask appropriate dimensions, if present
        if self.mask_cond_hint_original is not None:
            if self.sub_idxs is not None or self.mask_cond_hint is None or x_noisy.shape[2] * 8 != self.mask_cond_hint.shape[1] or x_noisy.shape[3] * 8 != self.mask_cond_hint.shape[2]:
                if self.mask_cond_hint is not None:
                    del self.mask_cond_hint
                self.mask_cond_hint = None
                # TODO: perform upscale on only the sub_idxs masks at a time instead of all to conserve RAM
                # resize mask and match batch count
                self.mask_cond_hint = prepare_mask_batch(self.mask_cond_hint_original, x_noisy.shape, multiplier=8)
                actual_latent_length = x_noisy.shape[0] // batched_number
                self.mask_cond_hint = comfy.utils.repeat_to_batch_size(self.mask_cond_hint, actual_latent_length if self.sub_idxs is None else self.full_latent_length)
                if self.sub_idxs is not None:
                    self.mask_cond_hint = self.mask_cond_hint[self.sub_idxs]
            # make cond_hint_mask length match x_noise
            if x_noisy.shape[0] != self.mask_cond_hint.shape[0]:
                self.mask_cond_hint = broadcast_image_to(self.mask_cond_hint, x_noisy.shape[0], batched_number)
            # default dtype to be same as x_noisy
            if dtype is None:
                dtype = x_noisy.dtype
            self.mask_cond_hint = self.mask_cond_hint.to(dtype=dtype).to(self.device)
        # prepare other masks
        self.prepare_tk_mask_cond_hint(x_noisy, t, cond, batched_number, dtype)

    def prepare_tk_mask_cond_hint(self, x_noisy: Tensor, t, cond, batched_number, dtype=None):
        return self._prepare_mask("tk_mask_cond_hint", self.current_timestep_keyframe.mask_hint_orig, x_noisy, t, cond, batched_number, dtype)

    def prepare_weight_mask_cond_hint(self, x_noisy: Tensor, t, cond, batched_number, dtype=None):
        return self._prepare_mask("weight_mask_cond_hint", self.current_timestep_keyframe.mask_hint_orig, x_noisy, t, cond, batched_number, dtype)

    def _prepare_mask(self, attr_name, orig_mask: Tensor, x_noisy: Tensor, t, cond, batched_number, dtype=None):
        if orig_mask is not None:
            out_mask = getattr(self, attr_name)
            if self.sub_idxs is not None or out_mask is None or x_noisy.shape[2] * 8 != out_mask.shape[1] or x_noisy.shape[3] * 8 != out_mask.shape[2]:
                self._reset_attr(attr_name)
                del out_mask
                # TODO: perform upscale on only the sub_idxs masks at a time instead of all to conserve RAM
                # resize mask and match batch count
                out_mask = prepare_mask_batch(orig_mask, x_noisy.shape, multiplier=8)
                actual_latent_length = x_noisy.shape[0] // batched_number
                out_mask = comfy.utils.repeat_to_batch_size(out_mask, actual_latent_length if self.sub_idxs is None else self.full_latent_length)
                if self.sub_idxs is not None:
                    out_mask = out_mask[self.sub_idxs]
            # make cond_hint_mask length match x_noise
            if x_noisy.shape[0] != out_mask.shape[0]:
                out_mask = broadcast_image_to(out_mask, x_noisy.shape[0], batched_number)
            # default dtype to be same as x_noisy
            if dtype is None:
                dtype = x_noisy.dtype
            setattr(self, attr_name, out_mask.to(dtype=dtype).to(self.device))
            del out_mask

    def _reset_attr(self, attr_name, new_value=None):
        if hasattr(self, attr_name):
            delattr(self, attr_name)
        setattr(self, attr_name, new_value)

    def cleanup_inject(self):
        self.base.cleanup()
        self.cleanup_advanced()

    def cleanup_advanced(self):
        self.sub_idxs = None
        self.full_latent_length = 0
        self.context_length = 0
        self.t = None
        self.batched_number = None
        self.weights = None
        self.latent_keyframes = None
        # timestep stuff
        self.current_timestep_keyframe = None
        self.next_timestep_keyframe = None
        self.current_timestep_index = -1
        # clear mask hints
        if self.mask_cond_hint is not None:
            del self.mask_cond_hint
            self.mask_cond_hint = None
        if self.tk_mask_cond_hint_original is not None:
            del self.tk_mask_cond_hint_original
            self.tk_mask_cond_hint_original = None
        if self.tk_mask_cond_hint is not None:
            del self.tk_mask_cond_hint
            self.tk_mask_cond_hint = None
        if self.weight_mask_cond_hint is not None:
            del self.weight_mask_cond_hint
            self.weight_mask_cond_hint = None
    
    def copy_to_advanced(self, copied: 'AdvancedControlBase'):
        copied.mask_cond_hint_original = self.mask_cond_hint_original
        copied.weights_override = self.weights_override
        copied.latent_keyframe_override = self.latent_keyframe_override


class ControlNetAdvanced(ControlNet, AdvancedControlBase):
    def __init__(self, control_model, timestep_keyframes: TimestepKeyframeGroup, global_average_pooling=False, device=None):
        super().__init__(control_model=control_model, global_average_pooling=global_average_pooling, device=device)
        AdvancedControlBase.__init__(self, super(), timestep_keyframes=timestep_keyframes, weights_default=ControlWeights.controlnet())

    def get_universal_weights(self) -> ControlWeights:
        raw_weights = [(self.weights.base_multiplier ** float(12 - i)) for i in range(13)]
        # TODO: account for masks?
        return ControlWeights.controlnet(raw_weights, self.weights.flip_weights)

    def get_control_advanced(self, x_noisy, t, cond, batched_number):
        # perform special version of get_control that supports sliding context and masks
        return self.sliding_get_control(x_noisy, t, cond, batched_number)

    def sliding_get_control(self, x_noisy: Tensor, t, cond, batched_number):
        control_prev = None
        if self.previous_controlnet is not None:
            control_prev = self.previous_controlnet.get_control(x_noisy, t, cond, batched_number)

        if self.timestep_range is not None:
            if t[0] > self.timestep_range[0] or t[0] < self.timestep_range[1]:
                if control_prev is not None:
                    return control_prev
                else:
                    return None

        output_dtype = x_noisy.dtype

        # make cond_hint appropriate dimensions
        # TODO: change this to not require cond_hint upscaling every step when self.sub_idxs are present
        if self.sub_idxs is not None or self.cond_hint is None or x_noisy.shape[2] * 8 != self.cond_hint.shape[2] or x_noisy.shape[3] * 8 != self.cond_hint.shape[3]:
            if self.cond_hint is not None:
                del self.cond_hint
            self.cond_hint = None
            # if self.cond_hint_original length greater or equal to real latent count, subdivide it before scaling
            if self.sub_idxs is not None and self.cond_hint_original.size(0) >= self.full_latent_length:
                self.cond_hint = comfy.utils.common_upscale(self.cond_hint_original[self.sub_idxs], x_noisy.shape[3] * 8, x_noisy.shape[2] * 8, 'nearest-exact', "center").to(self.control_model.dtype).to(self.device)
            else:
                self.cond_hint = comfy.utils.common_upscale(self.cond_hint_original, x_noisy.shape[3] * 8, x_noisy.shape[2] * 8, 'nearest-exact', "center").to(self.control_model.dtype).to(self.device)
        if x_noisy.shape[0] != self.cond_hint.shape[0]:
            self.cond_hint = broadcast_image_to(self.cond_hint, x_noisy.shape[0], batched_number)

        # prepare mask_cond_hint
        self.prepare_mask_cond_hint(x_noisy=x_noisy, t=t, cond=cond, batched_number=batched_number, dtype=self.control_model.dtype)

        context = cond['c_crossattn']
        # uses 'y' in new ComfyUI update
        y = cond.get('y', None)
        if y is None: # TODO: remove this in the future since no longer used by newest ComfyUI
            y = cond.get('c_adm', None)
        if y is not None:
            y = y.to(self.control_model.dtype)
        timestep = self.model_sampling_current.timestep(t)
        x_noisy = self.model_sampling_current.calculate_input(t, x_noisy)

        control = self.control_model(x=x_noisy.to(self.control_model.dtype), hint=self.cond_hint, timesteps=timestep.float(), context=context.to(self.control_model.dtype), y=y)
        return self.control_merge(None, control, control_prev, output_dtype)

    def copy(self):
        c = ControlNetAdvanced(self.control_model, self.timestep_keyframes, global_average_pooling=self.global_average_pooling)
        self.copy_to(c)
        self.copy_to_advanced(c)
        return c
    
    @staticmethod
    def from_vanilla(v: ControlNet, timestep_keyframe: TimestepKeyframeGroup=None) -> 'ControlNetAdvanced':
        return ControlNetAdvanced(control_model=v.control_model, timestep_keyframes=timestep_keyframe,
                                  global_average_pooling=v.global_average_pooling, device=v.device)


class T2IAdapterAdvanced(T2IAdapter, AdvancedControlBase):
    def __init__(self, t2i_model, timestep_keyframes: TimestepKeyframeGroup, channels_in, device=None):
        super().__init__(t2i_model=t2i_model, channels_in=channels_in, device=device)
        AdvancedControlBase.__init__(self, super(), timestep_keyframes=timestep_keyframes, weights_default=ControlWeights.t2iadapter())

    def get_universal_weights(self) -> ControlWeights:
        raw_weights = [(self.weights.base_multiplier ** float(7 - i)) for i in range(8)]
        raw_weights = [raw_weights[-8], raw_weights[-3], raw_weights[-2], raw_weights[-1]]
        raw_weights = get_properly_arranged_t2i_weights(raw_weights)
        # TODO: account for masks?
        return ControlWeights.t2iadapter(raw_weights, self.weights.flip_weights)

    def get_control_advanced(self, x_noisy, t, cond, batched_number):
        # prepare timestep and everything related
        self.prepare_current_timestep(t=t, batched_number=batched_number)
        try:
            # if sub indexes present, replace original hint with subsection
            if self.sub_idxs is not None:
                # cond hints
                full_cond_hint_original = self.cond_hint_original
                del self.cond_hint
                self.cond_hint = None
                self.cond_hint_original = full_cond_hint_original[self.sub_idxs]
            # mask hints
            self.prepare_mask_cond_hint(x_noisy=x_noisy, t=t, cond=cond, batched_number=batched_number)
            return super().get_control(x_noisy, t, cond, batched_number)
        finally:
            if self.sub_idxs is not None:
                # replace original cond hint
                self.cond_hint_original = full_cond_hint_original
                del full_cond_hint_original

    def copy(self):
        c = ControlLoraAdvanced(self.t2i_model, self.timestep_keyframes, self.channels_in)
        self.copy_to(c)
        self.copy_to_advanced(c)
        return c
    
    def cleanup(self):
        super().cleanup()
        self.cleanup_advanced()

    @staticmethod
    def from_vanilla(v: T2IAdapter, timestep_keyframe: TimestepKeyframeGroup=None) -> 'T2IAdapterAdvanced':
        return T2IAdapterAdvanced(t2i_model=v.t2i_model, timestep_keyframes=timestep_keyframe, channels_in=v.channels_in, device=v.device)


class ControlLoraAdvanced(ControlLora, AdvancedControlBase):
    def __init__(self, control_weights, timestep_keyframes: TimestepKeyframeGroup, global_average_pooling=False, device=None):
        super().__init__(control_weights=control_weights, global_average_pooling=global_average_pooling, device=device)
        AdvancedControlBase.__init__(self, super(), timestep_keyframes=timestep_keyframes, weights_default=ControlWeights.controllora())
        # use some functions from ControlNetAdvanced
        self.get_control_advanced = ControlNetAdvanced.get_control_advanced.__get__(self, type(self))
        self.sliding_get_control = ControlNetAdvanced.sliding_get_control.__get__(self, type(self))
    
    def get_universal_weights(self) -> ControlWeights:
        raw_weights = [(self.weights.base_multiplier ** float(9 - i)) for i in range(10)]
        # TODO: account for masks?
        return ControlWeights.controllora(raw_weights, self.weights.flip_weights)

    def copy(self):
        c = ControlLoraAdvanced(self.control_weights, self.timestep_keyframes, global_average_pooling=self.global_average_pooling)
        self.copy_to(c)
        self.copy_to_advanced(c)
        return c
    
    def cleanup(self):
        super().cleanup()
        self.cleanup_advanced()

    @staticmethod
    def from_vanilla(v: ControlLora, timestep_keyframe: TimestepKeyframeGroup=None) -> 'ControlLoraAdvanced':
        return ControlLoraAdvanced(control_weights=v.control_weights, timestep_keyframes=timestep_keyframe,
                                   global_average_pooling=v.global_average_pooling, device=v.device)


class ControlLLLiteAdvanced(ControlNet, AdvancedControlBase):
    def __init__(self, control_weights, timestep_keyframes: TimestepKeyframeGroup, device=None):
        AdvancedControlBase.__init__(self, super(), timestep_keyframes=timestep_keyframes, weights_default=ControlWeights.controllllite())


def load_controlnet(ckpt_path, timestep_keyframe: TimestepKeyframeGroup=None, model=None):
    control = comfy_cn.load_controlnet(ckpt_path, model=model)
    # TODO: support controlnet-lllite
    # if is None, see if is a non-vanilla ControlNet
    # if control is None:
    #     controlnet_data = comfy.utils.load_torch_file(ckpt_path, safe_load=True)
    #     # check if lllite
    #     if "lllite_unet" in controlnet_data:
    #         pass
    return convert_to_advanced(control, timestep_keyframe=timestep_keyframe)


def convert_to_advanced(control, timestep_keyframe: TimestepKeyframeGroup=None):
    # if already advanced, leave it be
    if is_advanced_controlnet(control):
        return control
    # if exactly ControlNet returned, transform it into ControlNetAdvanced
    if type(control) == ControlNet:
        return ControlNetAdvanced.from_vanilla(v=control, timestep_keyframe=timestep_keyframe)
    # if exactly ControlLora returned, transform it into ControlLoraAdvanced
    elif type(control) == ControlLora:
        return ControlLoraAdvanced.from_vanilla(v=control, timestep_keyframe=timestep_keyframe)
    # if T2IAdapter returned, transform it into T2IAdapterAdvanced
    elif isinstance(control, T2IAdapter):
        return T2IAdapterAdvanced.from_vanilla(v=control, timestep_keyframe=timestep_keyframe)
    # otherwise, leave it be - might be something I am not supporting yet
    return control


def is_advanced_controlnet(input_object):
    return hasattr(input_object, "sub_idxs")


# adapted from comfy/sample.py
def prepare_mask_batch(mask: Tensor, shape: Tensor, multiplier: int=1, match_dim1=False):
    mask = mask.clone()
    mask = torch.nn.functional.interpolate(mask.reshape((-1, 1, mask.shape[-2], mask.shape[-1])), size=(shape[2]*multiplier, shape[3]*multiplier), mode="bilinear")
    if match_dim1:
        mask = torch.cat([mask] * shape[1], dim=1)
    return mask

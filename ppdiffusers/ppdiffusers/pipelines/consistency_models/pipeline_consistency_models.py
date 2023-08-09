import paddle
from typing import Callable, List, Optional, Union
from ...models import UNet2DModel
from ...schedulers import CMStochasticIterativeScheduler
from ...utils import logging, randn_tensor, replace_example_docstring
from ..pipeline_utils import DiffusionPipeline, ImagePipelineOutput
logger = logging.get_logger(__name__)
EXAMPLE_DOC_STRING = """
    Examples:
        ```py
        >>> import paddle

        >>> from ppdiffusers import ConsistencyModelPipeline

        >>> # Load the cd_imagenet64_l2 checkpoint.
        >>> model_id_or_path = "openai/diffusers-cd_imagenet64_l2"
        >>> pipe = ConsistencyModelPipeline.from_pretrained(model_id_or_path, paddle_dtype=paddle.float16)

        >>> # Onestep Sampling
        >>> image = pipe(num_inference_steps=1).images[0]
        >>> image.save("cd_imagenet64_l2_onestep_sample.png")

        >>> # Onestep sampling, class-conditional image generation
        >>> # ImageNet-64 class label 145 corresponds to king penguins
        >>> image = pipe(num_inference_steps=1, class_labels=145).images[0]
        >>> image.save("cd_imagenet64_l2_onestep_sample_penguin.png")

        >>> # Multistep sampling, class-conditional image generation
        >>> # Timesteps can be explicitly specified; the particular timesteps below are from the original Github repo:
        >>> # https://github.com/openai/consistency_models/blob/main/scripts/launch.sh#L77
        >>> image = pipe(num_inference_steps=None, timesteps=[22, 0], class_labels=145).images[0]
        >>> image.save("cd_imagenet64_l2_multistep_sample_penguin.png")
        ```
"""


class ConsistencyModelPipeline(DiffusionPipeline):
    """
    Pipeline for unconditional or class-conditional image generation.

    This model inherits from [`DiffusionPipeline`]. Check the superclass documentation for the generic methods
    implemented for all pipelines (downloading, saving, running on a particular device, etc.).

    Args:
        unet ([`UNet2DModel`]):
            A `UNet2DModel` to denoise the encoded image latents.
        scheduler ([`SchedulerMixin`]):
            A scheduler to be used in combination with `unet` to denoise the encoded image latents. Currently only
            compatible with [`CMStochasticIterativeScheduler`].
    """

    def __init__(self,
                 unet: UNet2DModel,
                 scheduler: CMStochasticIterativeScheduler) -> None:
        super().__init__()
        self.register_modules(unet=unet, scheduler=scheduler)
        self.safety_checker = None

    def prepare_latents(self,
                        batch_size,
                        num_channels,
                        height,
                        width,
                        dtype,
                        generator,
                        latents=None):
        shape = batch_size, num_channels, height, width
        if isinstance(generator, list) and len(generator) != batch_size:
            raise ValueError(
                f'You have passed a list of generators of length {len(generator)}, but requested an effective batch size of {batch_size}. Make sure the batch size matches the length of the generators.'
            )
        if latents is None:
            latents = randn_tensor(shape, generator=generator, dtype=dtype)
        else:
            latents = latents.cast(dtype=dtype)
        latents = latents * self.scheduler.init_noise_sigma
        return latents

    def postprocess_image(self, sample: paddle.Tensor, output_type: str='pil'):
        if output_type not in ['pd', 'np', 'pil']:
            raise ValueError(
                f"output_type={output_type} is not supported. Make sure to choose one of ['pd', 'np', or 'pil']"
            )
        sample = (sample / 2 + 0.5).clip(min=0, max=1)
        if output_type == 'pd':
            return sample
        sample = sample.cpu().transpose(perm=[0, 2, 3, 1]).numpy()
        if output_type == 'np':
            return sample
        sample = self.numpy_to_pil(sample)
        return sample

    def prepare_class_labels(self, batch_size, class_labels=None):
        if self.unet.config.num_class_embeds is not None:
            if isinstance(class_labels, list):
                class_labels = paddle.to_tensor(
                    data=class_labels, dtype='int32')
            elif isinstance(class_labels, int):
                assert batch_size == 1, 'Batch size must be 1 if classes is an int'
                class_labels = paddle.to_tensor(
                    data=[class_labels], dtype='int32')
            elif class_labels is None:
                class_labels = paddle.randint(
                    low=0,
                    high=self.unet.config.num_class_embeds,
                    shape=(batch_size, ))
            class_labels = class_labels
        else:
            class_labels = None
        return class_labels

    def check_inputs(self, num_inference_steps, timesteps, latents, batch_size,
                     img_size, callback_steps):
        if num_inference_steps is None and timesteps is None:
            raise ValueError(
                'Exactly one of `num_inference_steps` or `timesteps` must be supplied.'
            )
        if num_inference_steps is not None and timesteps is not None:
            logger.warning(
                f'Both `num_inference_steps`: {num_inference_steps} and `timesteps`: {timesteps} are supplied; `timesteps` will be used over `num_inference_steps`.'
            )
        if latents is not None:
            expected_shape = batch_size, 3, img_size, img_size
            if latents.shape != expected_shape:
                raise ValueError(
                    f'The shape of latents is {latents.shape} but is expected to be {expected_shape}.'
                )
        if callback_steps is None or callback_steps is not None and (
                not isinstance(callback_steps, int) or callback_steps <= 0):
            raise ValueError(
                f'`callback_steps` has to be a positive integer but is {callback_steps} of type {type(callback_steps)}.'
            )

    @paddle.no_grad()
    def __call__(
            self,
            batch_size: int=1,
            class_labels: Optional[Union[paddle.Tensor, List[int], int]]=None,
            num_inference_steps: int=1,
            timesteps: List[int]=None,
            generator: Optional[Union[paddle.Generator, List[
                paddle.Generator]]]=None,
            latents: Optional[paddle.Tensor]=None,
            output_type: Optional[str]='pil',
            return_dict: bool=True,
            callback: Optional[Callable[[int, int, paddle.Tensor], None]]=None,
            callback_steps: int=1):
        """
        Args:
            batch_size (`int`, *optional*, defaults to 1):
                The number of images to generate.
            class_labels (`paddle.Tensor` or `List[int]` or `int`, *optional*):
                Optional class labels for conditioning class-conditional consistency models. Not used if the model is
                not class-conditional.
            num_inference_steps (`int`, *optional*, defaults to 1):
                The number of denoising steps. More denoising steps usually lead to a higher quality image at the
                expense of slower inference.
            timesteps (`List[int]`, *optional*):
                Custom timesteps to use for the denoising process. If not defined, equal spaced `num_inference_steps`
                timesteps are used. Must be in descending order.
            generator (`paddle.Generator`, *optional*):
                One or a list of paddle generator(s) to make generation deterministic.
            latents (`paddle.Tensor`, *optional*):
                Pre-generated noisy latents sampled from a Gaussian distribution, to be used as inputs for image
                generation. Can be used to tweak the same generation with different prompts. If not provided, a latents
                tensor is generated by sampling using the supplied random `generator`.
            output_type (`str`, *optional*, defaults to `"pil"`):
                The output format of the generated image. Choose between `PIL.Image` or `np.array`.
            return_dict (`bool`, *optional*, defaults to `True`):
                Whether or not to return a [`~pipelines.ImagePipelineOutput`] instead of a plain tuple.
            callback (`Callable`, *optional*):
                A function that calls every `callback_steps` steps during inference. The function is called with the
                following arguments: `callback(step: int, timestep: int, latents: paddle.Tensor)`.
            callback_steps (`int`, *optional*, defaults to 1):
                The frequency at which the `callback` function is called. If not specified, the callback is called at
                every step.

        Examples:

        Returns:
            [`~pipelines.ImagePipelineOutput`] or `tuple`:
                If `return_dict` is `True`, [`~pipelines.ImagePipelineOutput`] is returned, otherwise a `tuple` is
                returned where the first element is a list with the generated images.
        """
        img_size = self.unet.config.sample_size
        self.check_inputs(num_inference_steps, timesteps, latents, batch_size,
                          img_size, callback_steps)
        sample = self.prepare_latents(
            batch_size=batch_size,
            num_channels=self.unet.config.in_channels,
            height=img_size,
            width=img_size,
            dtype=self.unet.dtype,
            generator=generator,
            latents=latents)
        class_labels = self.prepare_class_labels(
            batch_size, class_labels=class_labels)
        if timesteps is not None:
            self.scheduler.set_timesteps(timesteps=timesteps)
            timesteps = self.scheduler.timesteps
            num_inference_steps = len(timesteps)
        else:
            self.scheduler.set_timesteps(num_inference_steps)
            timesteps = self.scheduler.timesteps
        with self.progress_bar(total=num_inference_steps) as progress_bar:
            for i, t in enumerate(timesteps):
                scaled_sample = self.scheduler.scale_model_input(sample, t)
                model_output = self.unet(
                    scaled_sample,
                    t,
                    class_labels=class_labels,
                    return_dict=False)[0]
                sample = self.scheduler.step(
                    model_output, t, sample, generator=generator)[0]
                progress_bar.update()
                if callback is not None and i % callback_steps == 0:
                    callback(i, t, sample)
        image = self.postprocess_image(sample, output_type=output_type)
        if not return_dict:
            return image,
        return ImagePipelineOutput(images=image)

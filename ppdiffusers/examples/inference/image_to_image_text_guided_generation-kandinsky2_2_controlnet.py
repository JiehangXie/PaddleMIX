# Copyright (c) 2023 PaddlePaddle Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import numpy as np
import paddle
from paddlenlp.transformers import pipeline

from ppdiffusers import (
    KandinskyV22ControlnetImg2ImgPipeline,
    KandinskyV22PriorEmb2EmbPipeline,
)
from ppdiffusers.utils import load_image


def make_hint(image, depth_estimator):
    image = depth_estimator(image)["depth"]
    image = np.array(image)
    image = image[:, :, None]
    image = np.concatenate([image, image, image], axis=2)
    detected_map = paddle.to_tensor(image).float() / 255.0
    hint = detected_map.permute(2, 0, 1)
    return hint


depth_estimator = pipeline("depth-estimation")
pipe_prior = KandinskyV22PriorEmb2EmbPipeline.from_pretrained(
    "kandinsky-community/kandinsky-2-2-prior", paddle_dtype=paddle.float16
)
pipe = KandinskyV22ControlnetImg2ImgPipeline.from_pretrained(
    "kandinsky-community/kandinsky-2-2-controlnet-depth", paddle_dtype=paddle.float16
)
img = load_image(
    "https://huggingface.co/datasets/hf-internal-testing/diffusers-images/resolve/main" "/kandinsky/cat.png"
).resize((768, 768))
hint = make_hint(img, depth_estimator).unsqueeze(0).half()
prompt = "A robot, 4k photo"
negative_prior_prompt = "lowres, text, error, cropped, worst quality, low quality, jpeg artifacts, ugly, duplicate, morbid, mutilated, out of frame, extra fingers, mutated hands, poorly drawn hands, poorly drawn face, mutation, deformed, blurry, dehydrated, bad anatomy, bad proportions, extra limbs, cloned face, disfigured, gross proportions, malformed limbs, missing arms, missing legs, extra arms, extra legs, fused fingers, too many fingers, long neck, username, watermark, signature"
generator = paddle.Generator().manual_seed(43)
img_emb = pipe_prior(prompt=prompt, image=img, strength=0.85, generator=generator)
negative_emb = pipe_prior(prompt=negative_prior_prompt, image=img, strength=1, generator=generator)
images = pipe(
    image=img,
    strength=0.5,
    image_embeds=img_emb.image_embeds,
    negative_image_embeds=negative_emb.image_embeds,
    hint=hint,
    num_inference_steps=50,
    generator=generator,
    height=768,
    width=768,
).images
images[0].save("image_to_image_text_guided_generation-kandinsky2_2_controlnet-result-robot_cat.png")

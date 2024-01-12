import torch
from transformers import AutoModelForCausalLM
from accelerate import dispatch_model


def _device_map(num_gpus, num_layers):
    per_gpu_layers = (num_layers + 2) / num_gpus

    device_map = {
        'transformer.wte': 0,
        'transformer.ln_f': 0,
        'lm_head': num_gpus-1
    }

    used = 1
    gpu_target = 0
    for i in range(num_layers):
        if used >= per_gpu_layers:
            gpu_target += 1
            used = 0 if gpu_target < num_gpus-1 else 1
        assert gpu_target < num_gpus
        device_map[f'transformer.h.{i}'] = gpu_target
        used += 1

    return device_map


def load_model_on_gpus(model_name_or_path, num_gpus: int = 2):
    num_devices = torch.cuda.device_count()

    if num_gpus == 1:
        model = AutoModelForCausalLM.from_pretrained(model_name_or_path, device_map='auto',
                                                     trust_remote_code=True).eval()
    elif 1 < num_gpus <= num_devices:
        model = AutoModelForCausalLM.from_pretrained(model_name_or_path, device_map='cpu',
                                                     trust_remote_code=True).eval()
        num_layers = model.config.num_hidden_layers
        device_map = _device_map(num_gpus, num_layers)
        print(device_map)
        model = dispatch_model(model, device_map=device_map)
    else:
        raise KeyError

    return model

import os
import json
import numpy as np
from loguru import logger
from transformers.trainer_callback import TrainerCallback


class SaveLossCallback(TrainerCallback):
    def __init__(self, loss_file_path=None):
        self.loss_list = []
        self.loss_metrics = {'train': []}
        if loss_file_path:
            os.makedirs(name=loss_file_path, exist_ok=True)
        self.loss_file = os.path.join(loss_file_path, "loss.json")
        logger.info(f"[SaveLossCallback] loss_file: {self.loss_file}")

    def on_epoch_end(self, args, state, control, **kwargs):
        # 自定义在每个epoch结束时执行的操作
        logger.info("-" * 80)

    def on_log(self, args, state, control, logs=None, **kwargs):
        # 检查logs中是否有loss和step信息，并打印它们
        if logs is not None and args.local_rank == 0:
            try:
                if "loss" in logs:
                    self.loss_list.append(float(logs['loss']))
                    step_per_epoch = int(state.max_steps / state.num_train_epochs)
                    metrics = {
                        'epoch': int(logs['epoch']) + 1,
                        'step': int(state.global_step - int(logs['epoch']) * step_per_epoch),
                        'global_step': state.global_step,
                        'loss': logs['loss'],
                        'lr': logs['learning_rate'],
                        'mean_loss': np.mean(self.loss_list)
                    }
                    logger.info(f"[metrics] {metrics}")
                    # logger.info(f"[step] {state.global_step}, [loss] {logs['loss']}, [logs] {logs}, [state] {state}")

                    self.loss_metrics['train'].append(metrics)

                    with open(self.loss_file, 'w', encoding="utf-8") as file:
                        json.dump(self.loss_metrics, file, indent=4, ensure_ascii=False)
            except Exception as e:
                logger.info(f"[on_log][logs] {logs}, [state] {state}")
                logger.exception(e)

# This code is based on the revised code from fastchat based on tatsu-lab/stanford_alpaca.


from dataclasses import dataclass, field
import json
import math
# import logging
import time
from loguru import logger
import os
from typing import Dict, Optional, List
import torch
from torch.utils.data import Dataset
from deepspeed import zero
from deepspeed.runtime.zero.partition_parameters import ZeroParamStatus
import transformers
from transformers import Trainer, GPTQConfig, deepspeed
from transformers.trainer_pt_utils import LabelSmoother
from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
from transformers import TrainerState, TrainerControl, PrinterCallback, ProgressCallback
from accelerate.utils import DistributedType
from utils import SaveLossCallback
from data_preprocess import data_preprocess

IGNORE_TOKEN_ID = LabelSmoother.ignore_index


@dataclass
class ModelArguments:
    model_name_or_path: Optional[str] = field(default="Taichu_1.8B_Chat")
    pretrained_model_path: Optional[str] = field(default="Taichu_1.8B_Chat")

@dataclass
class DataArguments:
    data_path: str = field(
        default=None, metadata={"help": "Path to the training data."}
    )
    eval_data_path: str = field(
        default=None, metadata={"help": "Path to the evaluation data."}
    )
    lazy_preprocess: bool = False
    data_exchange: bool = True
    preset_train_data_path: str = None
    preset_train_data_ratio: float = 1.0


@dataclass
class TrainingArguments(transformers.TrainingArguments):
    cache_dir: Optional[str] = field(default=None)
    optim: str = field(default="adamw_torch")
    model_max_length: int = field(
        default=8192,
        metadata={
            "help": "Maximum sequence length. Sequences will be right padded (and possibly truncated)."
        },
    )
    output_path: str = field(
        default="./output",
        metadata={"help": "The output directory where the model predictions and checkpoints will be written."},
    )
    output_dir: str = field(
        default="./output",
        metadata={"help": "The output directory where the model predictions and checkpoints will be written."},
    )
    use_lora: bool = False
    logging_first_step: bool = field(default=True, metadata={"help": "Log the first global_step"})


@dataclass
class LoraArguments:
    lora_r: int = 64
    lora_alpha: int = 16
    lora_dropout: float = 0.05
    lora_target_modules: List[str] = field(
        default_factory=lambda: ["c_attn", "c_proj", "w1", "w2"]
    )
    lora_weight_path: str = ""
    lora_bias: str = "none"
    q_lora: bool = False


def maybe_zero_3(param):
    if hasattr(param, "ds_id"):
        assert param.ds_status == ZeroParamStatus.NOT_AVAILABLE
        with zero.GatheredParameters([param]):
            param = param.data.detach().cpu().clone()
    else:
        param = param.detach().cpu().clone()
    return param


# Borrowed from peft.utils.get_peft_model_state_dict
def get_peft_state_maybe_zero_3(named_params, bias):
    if bias == "none":
        to_return = {k: t for k, t in named_params if "lora_" in k}
    elif bias == "all":
        to_return = {k: t for k, t in named_params if "lora_" in k or "bias" in k}
    elif bias == "lora_only":
        to_return = {}
        maybe_lora_bias = {}
        lora_bias_names = set()
        for k, t in named_params:
            if "lora_" in k:
                to_return[k] = t
                bias_name = k.split("lora_")[0] + "bias"
                lora_bias_names.add(bias_name)
            elif "bias" in k:
                maybe_lora_bias[k] = t
        for k, t in maybe_lora_bias:
            if bias_name in lora_bias_names:
                to_return[bias_name] = t
    else:
        raise NotImplementedError
    to_return = {k: maybe_zero_3(v) for k, v in to_return.items()}
    return to_return


local_rank = None

def rank0_print(*args):
    if local_rank == 0:
        logger.info(*args)


def safe_save_model_for_hf_trainer(trainer: transformers.Trainer, output_dir: str, bias="none"):
    """Collects the state dict and dump to disk."""
    # check if zero3 mode enabled
    if deepspeed.is_deepspeed_zero3_enabled():
        state_dict = trainer.model_wrapped._zero3_consolidated_16bit_state_dict()
    else:
        if trainer.args.use_lora:
            state_dict = get_peft_state_maybe_zero_3(
                trainer.model.named_parameters(), bias
            )
        else:
            state_dict = trainer.model.state_dict()
    if trainer.args.should_save and trainer.args.local_rank == 0:
        trainer._save(output_dir, state_dict=state_dict)


from peft import AutoPeftModelForCausalLM
from transformers import AutoTokenizer


def merge_save_model(trainer: transformers.Trainer, path_to_adapter, new_model_directory, device_map):
    if trainer.args.should_save and trainer.args.local_rank == 0:
        logger.info("[merge_save_model] start")
        model = AutoPeftModelForCausalLM.from_pretrained(
            path_to_adapter,  # path to the output directory
            device_map=device_map,
            trust_remote_code=True
        ).eval()

        merged_model = model.merge_and_unload()
        # max_shard_size and safe serialization are not necessary.
        # They respectively work for sharding checkpoint and save the model to safetensors
        merged_model.save_pretrained(new_model_directory, max_shard_size="2048MB", safe_serialization=True)

        tokenizer = AutoTokenizer.from_pretrained(
            path_to_adapter,  # path to the output directory
            trust_remote_code=True
        )
        tokenizer.save_pretrained(new_model_directory)
        logger.info("[merge_save_model] end")


def preprocess(
    sources,
    tokenizer: transformers.PreTrainedTokenizer,
    max_len: int,
    system_message: str = "You are a helpful assistant."
) -> Dict:
    roles = {"user": "<|im_start|>user", "assistant": "<|im_start|>assistant"}

    im_start = tokenizer.im_start_id
    im_end = tokenizer.im_end_id
    nl_tokens = tokenizer('\n').input_ids
    _system = tokenizer('system').input_ids + nl_tokens
    _user = tokenizer('user').input_ids + nl_tokens
    _assistant = tokenizer('assistant').input_ids + nl_tokens

    # Apply prompt templates
    input_ids, targets = [], []
    for i, source in enumerate(sources):
        # print("[{}] [source] {}".format(i, source))
        if roles[source[0]["from"]] != roles["user"]:
            source = source[1:]

        input_id, target = [], []
        system = [im_start] + _system + tokenizer(system_message).input_ids + [im_end] + nl_tokens
        input_id += system
        target += [im_start] + [IGNORE_TOKEN_ID] * (len(system)-3) + [im_end] + nl_tokens
        assert len(input_id) == len(target)
        for j, sentence in enumerate(source):
            role = roles[sentence["from"]]
            _input_id = tokenizer(role).input_ids + nl_tokens + \
                tokenizer(sentence["value"]).input_ids + [im_end] + nl_tokens
            input_id += _input_id
            if role == '<|im_start|>user':
                _target = [im_start] + [IGNORE_TOKEN_ID] * (len(_input_id)-3) + [im_end] + nl_tokens
            elif role == '<|im_start|>assistant':
                _target = [im_start] + [IGNORE_TOKEN_ID] * len(tokenizer(role).input_ids) + \
                    _input_id[len(tokenizer(role).input_ids)+1:-2] + [im_end] + nl_tokens
            else:
                raise NotImplementedError
            target += _target
        assert len(input_id) == len(target)
        input_id += [tokenizer.pad_token_id] * (max_len - len(input_id))
        target += [IGNORE_TOKEN_ID] * (max_len - len(target))
        input_ids.append(input_id[:max_len])
        targets.append(target[:max_len])
    input_ids = torch.tensor(input_ids, dtype=torch.int)
    targets = torch.tensor(targets, dtype=torch.int)
    # print("[input_ids] {}".format(input_ids))
    # print("[targets] {}".format(targets))

    return dict(
        input_ids=input_ids,
        labels=targets,
        attention_mask=input_ids.ne(tokenizer.pad_token_id),
    )


class SupervisedDataset(Dataset):
    """Dataset for supervised fine-tuning."""

    def __init__(self, raw_data, tokenizer: transformers.PreTrainedTokenizer, max_len: int):
        super(SupervisedDataset, self).__init__()

        rank0_print("Formatting inputs...")
        sources = [example["conversations"] for example in raw_data]
        data_dict = preprocess(sources, tokenizer, max_len)

        self.input_ids = data_dict["input_ids"]
        self.labels = data_dict["labels"]
        self.attention_mask = data_dict["attention_mask"]

    def __len__(self):
        return len(self.input_ids)

    def __getitem__(self, i) -> Dict[str, torch.Tensor]:
        return dict(
            input_ids=self.input_ids[i],
            labels=self.labels[i],
            attention_mask=self.attention_mask[i],
        )


class LazySupervisedDataset(Dataset):
    """Dataset for supervised fine-tuning."""

    def __init__(self, raw_data, tokenizer: transformers.PreTrainedTokenizer, max_len: int):
        super(LazySupervisedDataset, self).__init__()
        self.tokenizer = tokenizer
        self.max_len = max_len

        rank0_print("Formatting inputs...Skip in lazy mode")
        self.tokenizer = tokenizer
        self.raw_data = raw_data
        self.cached_data_dict = {}

    def __len__(self):
        return len(self.raw_data)

    def __getitem__(self, i) -> Dict[str, torch.Tensor]:
        if i in self.cached_data_dict:
            return self.cached_data_dict[i]

        ret = preprocess([self.raw_data[i]["conversations"]], self.tokenizer, self.max_len)
        ret = dict(
            input_ids=ret["input_ids"][0],
            labels=ret["labels"][0],
            attention_mask=ret["attention_mask"][0],
        )
        self.cached_data_dict[i] = ret

        return ret


def make_supervised_data_module(
    tokenizer: transformers.PreTrainedTokenizer, data_args, max_len,
) -> Dict:
    """Make dataset and collator for supervised fine-tuning."""
    dataset_cls = (
        LazySupervisedDataset if data_args.lazy_preprocess else SupervisedDataset
    )
    rank0_print("Loading data...")

    train_json = json.load(open(data_args.data_path, "r"))
    train_dataset = dataset_cls(train_json, tokenizer=tokenizer, max_len=max_len)

    if data_args.eval_data_path:
        eval_json = json.load(open(data_args.eval_data_path, "r"))
        eval_dataset = dataset_cls(eval_json, tokenizer=tokenizer, max_len=max_len)
    else:
        eval_dataset = None

    return dict(train_dataset=train_dataset, eval_dataset=eval_dataset)


def train_params_preprocess(training_args:TrainingArguments, data_args: DataArguments) -> TrainingArguments:
    try:
        with open(data_args.data_path, mode="r", encoding="utf-8") as fr:
            train_data_list = json.load(fp=fr)
            training_args._frozen = False
            # if len(train_data_list) <= 20:
            #     training_args.gradient_accumulation_steps = 1
            # elif len(train_data_list) <= 50:
            #     training_args.gradient_accumulation_steps = 2
            # elif len(train_data_list) <= 100:
            #     training_args.gradient_accumulation_steps = 4
            # else:
            #     training_args.gradient_accumulation_steps = 8
            training_args.gradient_accumulation_steps = 1
            training_args._frozen = True

        logger.info("[train_params_preprocess] train_data_len: {}, gradient_accumulation_steps: {}".format(
            len(train_data_list), training_args.gradient_accumulation_steps))
    except Exception as e:
        logger.exception(e)

    return training_args


def train():
    global local_rank

    parser = transformers.HfArgumentParser(
        (ModelArguments, DataArguments, TrainingArguments, LoraArguments)
    )
    logger.info("=" * 80)
    logger.info("[train] parser: {}".format(parser))
    (
        model_args,
        data_args,
        training_args,
        lora_args,
    ) = parser.parse_args_into_dataclasses()

    local_rank = training_args.local_rank
    rank0_print("=" * 80)
    logger.info("[local_rank] {}".format(local_rank))

    rank0_print("=" * 80)
    rank0_print("[train] model_args: {}".format(model_args))
    rank0_print("[train] data_args: {}".format(data_args))
    rank0_print("[train] training_args: {}".format(training_args))
    rank0_print("[train] lora_args: {}".format(lora_args))

    rank0_print("=" * 80)
    if data_args.data_path.endswith(".json") is False:
        rank0_print("[data_preprocess] data_path before: {}".format(data_args.data_path))
        data_args.data_path = os.path.join(data_args.data_path, "result.json")
        rank0_print("[data_preprocess] data_path after: {}".format(data_args.data_path))
    if data_args.preset_train_data_path and data_args.preset_train_data_path.endswith(".json") is False:
        rank0_print("[data_preprocess] preset_train_data_path before: {}".format(data_args.preset_train_data_path))
        data_args.preset_train_data_path = os.path.join(data_args.preset_train_data_path, "result.json")
        rank0_print("[data_preprocess] preset_train_data_path after: {}".format(data_args.preset_train_data_path))

    if os.path.exists(data_args.data_path) is False:
        rank0_print("[data_preprocess] 文件: {}, 不存在".format(data_args.data_path))
        exit(99)

    if data_args.data_exchange is True:
        rank0_print("[data_exchange] start data_path before: {}".format(data_args.data_path))
        replace_dict = {"question": "user", "answer": "assistant"}

        if data_args.data_path.endswith(".json"):
            output_path = "./train_data.json"
            data_preprocess(input_path=data_args.data_path,
                            output_path=output_path,
                            replace_dict=replace_dict,
                            preset_data_path=data_args.preset_train_data_path,
                            preset_data_ratio=data_args.preset_train_data_ratio)
            data_args.data_path = output_path
        else:
            rank0_print("[data_exchange] data_path: {}, 没有以 .json 结尾".format(data_args.data_path))
            exit(99)

        rank0_print("[data_exchange] data_path after: {}".format(data_args.data_path))

    rank0_print("=" * 80)
    data_args.data_dir = data_args.data_path
    rank0_print("[data_exchange] data_dir after: {}".format(data_args.data_dir))

    rank0_print("=" * 80)
    rank0_print("[train][train_params_preprocess] start")
    training_args = train_params_preprocess(training_args=training_args, data_args=data_args)
    rank0_print("[train][train_params_preprocess] end, gradient_accumulation_steps: {}".format(
        training_args.gradient_accumulation_steps))

    rank0_print("=" * 80)
    # This serves for single-gpu qlora.
    if getattr(training_args, 'deepspeed', None) and int(os.environ.get("WORLD_SIZE", 1))==1:
        training_args.distributed_state.distributed_type = DistributedType.DEEPSPEED

    # device_map = "auto"
    device_map = None
    world_size = int(os.environ.get("WORLD_SIZE", 1))
    ddp = world_size != 1
    if lora_args.q_lora:
        device_map = {"": int(os.environ.get("LOCAL_RANK") or 0)} if ddp else "auto"
        if len(training_args.fsdp) > 0 or deepspeed.is_deepspeed_zero3_enabled():
            logger.warning(
                "FSDP or ZeRO3 are incompatible with QLoRA."
            )

    # Set RoPE scaling factor
    config = transformers.AutoConfig.from_pretrained(
        # model_args.model_name_or_path,
        model_args.pretrained_model_path,
        cache_dir=training_args.cache_dir,
        trust_remote_code=True,
    )
    config.use_cache = False

    # Load model and tokenizer
    model = transformers.AutoModelForCausalLM.from_pretrained(
        # model_args.model_name_or_path,
        model_args.pretrained_model_path,
        config=config,
        cache_dir=training_args.cache_dir,
        device_map=device_map,
        trust_remote_code=True,
        quantization_config=GPTQConfig(
            bits=4, disable_exllama=True
        )
        if training_args.use_lora and lora_args.q_lora
        else None,
    )
    tokenizer = transformers.AutoTokenizer.from_pretrained(
        # model_args.model_name_or_path,
        model_args.pretrained_model_path,
        cache_dir=training_args.cache_dir,
        model_max_length=training_args.model_max_length,
        padding_side="right",
        use_fast=False,
        trust_remote_code=True,
    )

    if hasattr(tokenizer, "pad_token_id"):
        if tokenizer.pad_token_id is None and hasattr(tokenizer, "eod_id"):
            tokenizer.pad_token_id = tokenizer.eod_id
    logger.info("[tokenizer] tokenizer.pad_token_id: {}".format(tokenizer.pad_token_id))

    if training_args.use_lora:
        # if lora_args.q_lora or 'chat' in model_args.model_name_or_path.lower():
        if lora_args.q_lora or 'chat' in model_args.pretrained_model_path.lower():
            modules_to_save = None
        else:
            modules_to_save = ["wte", "lm_head"]
        lora_config = LoraConfig(
            r=lora_args.lora_r,
            lora_alpha=lora_args.lora_alpha,
            target_modules=lora_args.lora_target_modules,
            lora_dropout=lora_args.lora_dropout,
            bias=lora_args.lora_bias,
            task_type="CAUSAL_LM",
            modules_to_save=modules_to_save  # This argument serves for adding new tokens.
        )
        if lora_args.q_lora:
            model = prepare_model_for_kbit_training(
                model, use_gradient_checkpointing=training_args.gradient_checkpointing
            )

        model = get_peft_model(model, lora_config)

        # Print peft trainable params
        model.print_trainable_parameters()

        if training_args.gradient_checkpointing:
            model.enable_input_require_grads()

    # Load data
    data_module = make_supervised_data_module(
        tokenizer=tokenizer, data_args=data_args, max_len=training_args.model_max_length
    )

    # Start trainner
    trainer = Trainer(
        model=model, tokenizer=tokenizer, args=training_args, **data_module
    )

    # delete useless callback
    trainer.pop_callback(callback=PrinterCallback)
    trainer.pop_callback(callback=ProgressCallback)
    # add custom callback
    save_loss_callback = SaveLossCallback(
        loss_file_path=os.path.join(training_args.output_path, "metrics"))
    trainer.add_callback(callback=save_loss_callback)

    trainer.train()
    trainer.save_state()

    rank0_print("=" * 80)
    rank0_print("[save_model] start")
    tmp_output_path = os.path.join(training_args.output_path, "tmp")
    safe_save_model_for_hf_trainer(trainer=trainer, output_dir=tmp_output_path, bias=lora_args.lora_bias)
    merge_save_model(trainer=trainer,
                     path_to_adapter=tmp_output_path,
                     new_model_directory=training_args.output_path,
                     device_map=device_map)
    rank0_print("=" * 80)
    rank0_print("[train] finish !!!")


if __name__ == "__main__":
    start = time.time()
    train()
    end = time.time()
    rank0_print("[time_used] {}".format(end-start))

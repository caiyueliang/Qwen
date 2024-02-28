import json
import os
import random
import pandas as pd
import argparse
from loguru import logger


def sample_preset_data(src_lines: list, preset_lines: list, preset_data_ratio: float) -> list:
    logger.info("[src_lines] len: {}; [preset_lines] len: {}; [ratio] {}".format(
        len(src_lines), len(preset_lines), preset_data_ratio))

    num = int(len(src_lines) * preset_data_ratio)
    num = num if num < len(preset_lines) else len(preset_lines)
    preset_lines = random.sample(preset_lines, num)

    src_lines = src_lines + preset_lines
    logger.info("[src_lines] len: {}; [preset_lines] len: {}; [ratio] {}".format(
        len(src_lines), len(preset_lines), preset_data_ratio))
    return src_lines


def data_exchange(src_data_list: list, replace_dict: dict):
    new_data_list = list()
    src_data_len = len(src_data_list)
    logger.info("[data_exchange] src_data_len: {}".format(src_data_len))

    i = 0
    for conversation_text in src_data_list:
        try:
            if i == 0:
                logger.info("[data_exchange][conversation_text] {}".format(conversation_text))
            conversation_dict = json.loads(conversation_text)
            if i == 0:
                logger.info("[data_exchange][conversation_dict] before: {}".format(conversation_dict))

            if "conversations" in conversation_dict.keys():
                for dialog_dict in conversation_dict["conversations"]:
                    if "from" in dialog_dict.keys():
                        if dialog_dict["from"] in replace_dict.keys():
                            dialog_dict["from"] = replace_dict[dialog_dict["from"]]
                        else:
                            logger.warning("[data_exchange][dialog_dict] {}，from非法".format(dialog_dict))
            else:
                logger.warning("[data_exchange][conversation_dict]{}，不存在：conversations".format(conversation_dict))

            if i == 0:
                logger.info("[data_exchange][conversation_dict] after: {}".format(conversation_dict))

            i += 1
            new_data_list.append(conversation_dict)
        except Exception as e:
            logger.exception(e)
            logger.info("[data_exchange][conversation_text] {}".format(conversation_text))

    logger.info("[data_exchange] finish, src_data_len: {}, new_data_len: {}".format(
        src_data_len, len(new_data_list)))

    return new_data_list


def data_preprocess(input_path, output_path, replace_dict, preset_data_path=None, preset_data_ratio=1.0):
    if os.path.exists(input_path) is True:
        with open(input_path, mode="r", encoding="utf-8") as fr:
            src_lines = fr.readlines()

        if preset_data_path is not None:            # 如果preset_data_path不为空，则会对数据进行扩展
            if os.path.exists(preset_data_path) is True:
                with open(preset_data_path, mode="r", encoding="utf-8") as fr:
                    preset_lines = fr.readlines()
                    src_lines = sample_preset_data(src_lines=src_lines,
                                                   preset_lines=preset_lines,
                                                   preset_data_ratio=preset_data_ratio)
            else:
                logger.warning("[preprocess] preset_data_path: {}，文件不存在".format(preset_data_path))

        new_data_list = data_exchange(src_data_list=src_lines, replace_dict=replace_dict)
        with open(output_path, mode="w", encoding="utf-8") as fw:
            json.dump(obj=new_data_list, fp=fw, ensure_ascii=False, indent=4)
        logger.info("[preprocess] save finish, output_path: {}".format(output_path))
    else:
        logger.warning("[preprocess] input_path: {}，文件不存在".format(input_path))


def parse_argvs():
    parser = argparse.ArgumentParser(description='data preprocess')
    parser.add_argument("--input_data", type=str, default="./data/开发数据集统一格式/07-医疗问诊/result.json")
    parser.add_argument("--output_data", type=str, default="./result_temp.json")
    # parser.add_argument("--replace_dict", type={}, default={"question": "user", "answer": "assistant"})

    args = parser.parse_args()
    logger.info('[args] {}'.format(args))

    return parser, args


if __name__ == "__main__":
    parser, args = parse_argvs()

    replace_dict = {"question": "user", "answer": "assistant"}
    preprocess(input_path=args.input_data, output_path=args.output_data, replace_dict=replace_dict)

    # new_data_list = list()
    # with open(args.input_data, mode="r", encoding="utf-8") as fr:
    #     file_line = fr.readlines()
    #
    # src_len = len(file_line)
    # logger.info("[data_preprocess] file_line len: {}".format(src_len))
    #
    # i = 0
    # for conversation_text in file_line:
    #     try:
    #         if i == 0:
    #             logger.info("[conversation_text] {}".format(conversation_text))
    #         conversation_dict = json.loads(conversation_text)
    #         if i == 0:
    #             logger.info("[conversation_dict] before: {}".format(conversation_dict))
    #
    #         if "conversations" in conversation_dict.keys():
    #             for dialog_dict in conversation_dict["conversations"]:
    #                 if "from" in dialog_dict.keys():
    #                     if dialog_dict["from"] in replace_dict.keys():
    #                         dialog_dict["from"] = replace_dict[dialog_dict["from"]]
    #                     else:
    #                         logger.warning("[dialog_dict] {}，from非法".format(dialog_dict))
    #         else:
    #             logger.warning("[conversation_dict]{}，不存在：conversations".format(conversation_dict))
    #
    #         if i == 0:
    #             logger.info("[conversation_dict] after: {}".format(conversation_dict))
    #
    #         i += 1
    #         new_data_list.append(conversation_dict)
    #     except Exception as e:
    #         logger.exception(e)
    #         logger.info("[conversation_text] {}".format(conversation_text))
    #
    # logger.warning("[data_preprocess] finish, src_len: {}, dst_len: {}".format(src_len, len(new_data_list)))
    # with open(args.output_data, mode="w", encoding="utf-8") as fw:
    #     json.dump(obj=new_data_list, fp=fw, ensure_ascii=False, indent=4)
    #
    # logger.warning("[data_preprocess] save finish, output_path: {}".format(args.output_data))


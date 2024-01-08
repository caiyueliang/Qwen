import json
import os
import pandas as pd
import argparse
from loguru import logger


def data_exchange(input_path, output_path, replace_dict):
    logger.info("[data_exchange] input_path: {}".format(input_path))
    logger.info("[data_exchange] output_path: {}".format(output_path))
    logger.info("[data_exchange] replace_dict: {}".format(replace_dict))

    new_data_list = list()
    with open(input_path, mode="r", encoding="utf-8") as fr:
        file_line = fr.readlines()

    src_len = len(file_line)
    logger.info("[data_preprocess] file_line len: {}".format(src_len))

    i = 0
    for conversation_text in file_line:
        try:
            if i == 0:
                logger.info("[conversation_text] {}".format(conversation_text))
            conversation_dict = json.loads(conversation_text)
            if i == 0:
                logger.info("[conversation_dict] before: {}".format(conversation_dict))

            if "conversations" in conversation_dict.keys():
                for dialog_dict in conversation_dict["conversations"]:
                    if "from" in dialog_dict.keys():
                        if dialog_dict["from"] in replace_dict.keys():
                            dialog_dict["from"] = replace_dict[dialog_dict["from"]]
                        else:
                            logger.warning("[dialog_dict] {}，from非法".format(dialog_dict))
            else:
                logger.warning("[conversation_dict]{}，不存在：conversations".format(conversation_dict))

            if i == 0:
                logger.info("[conversation_dict] after: {}".format(conversation_dict))

            i += 1
            new_data_list.append(conversation_dict)
        except Exception as e:
            logger.exception(e)
            logger.info("[conversation_text] {}".format(conversation_text))

    logger.warning("[data_exchange] finish, src_len: {}, dst_len: {}".format(src_len, len(new_data_list)))
    with open(output_path, mode="w", encoding="utf-8") as fw:
        json.dump(obj=new_data_list, fp=fw, ensure_ascii=False, indent=4)

    logger.info("[data_exchange] save finish, output_path: {}".format(output_path))


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
    data_exchange(input_path=args.input_data, output_path=args.output_data, replace_dict=replace_dict)

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


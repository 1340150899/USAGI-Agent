from enum import StrEnum


class StageType(StrEnum):
    PRE_RECALL = "pre_recall"
    RECALL = "recall"
    CONTEXT_BUILD = "context_build"
    MODEL = "model"
    RESULT_PROCESS = "result_process"
    END = "end"

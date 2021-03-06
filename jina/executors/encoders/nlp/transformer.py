__copyright__ = "Copyright (c) 2020 Jina AI Limited. All rights reserved."
__license__ = "Apache-2.0"

import os

import numpy as np

from ..helper import reduce_mean, reduce_max, reduce_min, reduce_cls
from ...decorators import batching, as_ndarray
from ...frameworks import BaseFrameworkExecutor, BaseTorchExecutor, BaseTFExecutor


class BaseTransformerEncoder(BaseFrameworkExecutor):
    """
    :class:`TransformerTextEncoder` encodes data from an array of string in size `B` into an ndarray in size `B x D`.
    """

    def __init__(self,
                 model_name: str = 'bert-base-uncased',
                 pooling_strategy: str = 'mean',
                 max_length: int = 64,
                 model_path: str = 'transformer',
                 *args, **kwargs):
        """

        :param model_name: the name of the model. Supported models include 'bert-base-uncased', 'openai-gpt', 'gpt2',
            'xlm-mlm-enfr-1024', 'distilbert-base-cased', 'roberta-base', 'xlm-roberta-base', 'flaubert-base-cased',
            'camembert-base', 'ctrl'.
        :param pooling_strategy: the strategy to merge the word embeddings into the chunk embedding. Supported
            strategies include 'cls', 'mean', 'max', 'min'.
        :param max_length: the max length to truncate the tokenized sequences to.
        :param model_path: the path of the encoder model. If a valid path is given, the encoder will be loaded from the
            given path.
        """
        super().__init__(*args, **kwargs)
        self.model_name = model_name
        self.pooling_strategy = pooling_strategy
        self.max_length = max_length
        self.raw_model_path = model_path

    def _init_tokenizer(self):
        from transformers import BertTokenizer, OpenAIGPTTokenizer, GPT2Tokenizer, \
            XLNetTokenizer, XLMTokenizer, DistilBertTokenizer, RobertaTokenizer, XLMRobertaTokenizer, \
            FlaubertTokenizer, CamembertTokenizer, CTRLTokenizer

        tokenizer_dict = {
            'bert-base-uncased': BertTokenizer,
            'openai-gpt': OpenAIGPTTokenizer,
            'gpt2': GPT2Tokenizer,
            'xlnet-base-cased': XLNetTokenizer,
            'xlm-mlm-enfr-1024': XLMTokenizer,
            'distilbert-base-cased': DistilBertTokenizer,
            'roberta-base': RobertaTokenizer,
            'xlm-roberta-base': XLMRobertaTokenizer,
            'flaubert-base-cased': FlaubertTokenizer,
            'camembert-base': CamembertTokenizer,
            'ctrl': CTRLTokenizer
        }

        if self.model_name not in tokenizer_dict:
            self.logger.error('{} not in our supports: {}'.format(self.model_name, ','.join(tokenizer_dict.keys())))
            raise ValueError

        self._tmp_model_path = self.model_name
        if os.path.exists(self.model_abspath):
            self._tmp_model_path = self.model_abspath

        self.tokenizer = tokenizer_dict[self.model_name].from_pretrained(self._tmp_model_path)
        self.tokenizer.padding_side = 'right'

        if self.model_name in (
                'bert-base-uncased', 'distilbert-base-cased', 'roberta-base', 'xlm-roberta-base', 'flaubert-base-cased',
                'camembert-base'):
            self.cls_pos = 'head'
        elif self.model_name in ('xlnet-base-cased'):
            self.cls_pos = 'tail'

        if self.model_name in ('openai-gpt', 'gpt2', 'xlm-mlm-enfr-1024', 'xlnet-base-cased'):
            self.tokenizer.pad_token = '<PAD>'

    @batching
    @as_ndarray
    def encode(self, data: 'np.ndarray', *args, **kwargs) -> 'np.ndarray':
        """

        :param data: a 1d array of string type in size `B`
        :return: an ndarray in size `B x D`
        """
        token_ids_batch = []
        mask_ids_batch = []
        for c_idx in range(data.shape[0]):
            token_ids = self.tokenizer.encode(
                data[c_idx], pad_to_max_length=True, max_length=self.max_length)
            mask_ids = [0 if t == self.tokenizer.pad_token_id else 1 for t in token_ids]
            token_ids_batch.append(token_ids)
            mask_ids_batch.append(mask_ids)
        token_ids_batch = self.array2tensor(token_ids_batch)
        mask_ids_batch = self.array2tensor(mask_ids_batch)
        with self._sess_func():
            seq_output, *extra_output = self.model(token_ids_batch, attention_mask=mask_ids_batch)
            _mask_ids_batch = self.tensor2array(mask_ids_batch)
            _seq_output = self.tensor2array(seq_output)
            if self.pooling_strategy == 'cls':
                if self.model_name in ('bert-base-uncased', 'roberta-base'):
                    output = self.tensor2array(extra_output[0])
                else:
                    output = reduce_cls(_seq_output, _mask_ids_batch, self.cls_pos)
            elif self.pooling_strategy == 'mean':
                output = reduce_mean(_seq_output, _mask_ids_batch)
            elif self.pooling_strategy == 'max':
                output = reduce_max(_seq_output, _mask_ids_batch)
            elif self.pooling_strategy == 'min':
                output = reduce_min(_seq_output, _mask_ids_batch)
            else:
                self.logger.error("pooling strategy not found: {}".format(self.pooling_strategy))
                raise NotImplementedError
        return output

    def __getstate__(self):
        if not os.path.exists(self.model_abspath):
            self.logger.info("create folder for saving transformer models: {}".format(self.model_abspath))
            os.mkdir(self.model_abspath)
        self.model.save_pretrained(self.model_abspath)
        self.tokenizer.save_pretrained(self.model_abspath)
        return super().__getstate__()

    @property
    def model_abspath(self) -> str:
        """Get the file path of the encoder model storage

        """
        return self.get_file_from_workspace(self.raw_model_path)

    def post_init(self):
        self._init_tokenizer()
        self._init_model()

    def _init_model(self):
        raise NotImplementedError

    def array2tensor(self, array):
        return self._tensor_func(array)

    def tensor2array(self, tensor):
        return tensor.numpy()


class TransformerTFEncoder(BaseTFExecutor, BaseTransformerEncoder):
    """
    Internally, TransformerTFEncoder wraps the tensorflow-version of transformers from huggingface.
    """

    def _init_model(self):
        self.to_device()
        import tensorflow as tf
        from transformers import TFBertModel, TFOpenAIGPTModel, TFGPT2Model, TFXLNetModel, TFXLMModel, \
            TFDistilBertModel, TFRobertaModel, TFXLMRobertaModel, TFCamembertModel, TFCTRLModel
        model_dict = {
            'bert-base-uncased': TFBertModel,
            'openai-gpt': TFOpenAIGPTModel,
            'gpt2': TFGPT2Model,
            'xlnet-base-cased': TFXLNetModel,
            'xlm-mlm-enfr-1024': TFXLMModel,
            'distilbert-base-cased': TFDistilBertModel,
            'roberta-base': TFRobertaModel,
            'xlm-roberta-base': TFXLMRobertaModel,
            'camembert-base': TFCamembertModel,
            'ctrl': TFCTRLModel
        }
        self.model = model_dict[self.model_name].from_pretrained(self._tmp_model_path)
        self._tensor_func = tf.constant
        self._sess_func = tf.GradientTape
        if self.model_name in ('xlnet-base-cased', 'openai-gpt', 'gpt2', 'xlm-mlm-enfr-1024'):
            self.model.resize_token_embeddings(len(self.tokenizer))


class TransformerTorchEncoder(BaseTorchExecutor, BaseTransformerEncoder):
    """
    Internally, TransformerTorchEncoder wraps the pytorch-version of transformers from huggingface.
    """

    def _init_model(self):
        import torch
        from transformers import BertModel, OpenAIGPTModel, GPT2Model, XLNetModel, XLMModel, DistilBertModel, \
            RobertaModel, XLMRobertaModel, FlaubertModel, CamembertModel, CTRLModel
        model_dict = {
            'bert-base-uncased': BertModel,
            'openai-gpt': OpenAIGPTModel,
            'gpt2': GPT2Model,
            'xlnet-base-cased': XLNetModel,
            'xlm-mlm-enfr-1024': XLMModel,
            'distilbert-base-cased': DistilBertModel,
            'roberta-base': RobertaModel,
            'xlm-roberta-base': XLMRobertaModel,
            'flaubert-base-cased': FlaubertModel,
            'camembert-base': CamembertModel,
            'ctrl': CTRLModel
        }
        self.model = model_dict[self.model_name].from_pretrained(self._tmp_model_path)
        self._tensor_func = torch.tensor
        self._sess_func = torch.no_grad
        if self.model_name in ('xlnet-base-cased', 'openai-gpt', 'gpt2', 'xlm-mlm-enfr-1024'):
            self.model.resize_token_embeddings(len(self.tokenizer))
        self.to_device(self.model)

    def array2tensor(self, array):
        tensor = super().array2tensor(array)
        if self.on_gpu:
            tensor = tensor.cuda()
        return tensor

    def tensor2array(self, tensor):
        if self.on_gpu:
            tensor = tensor.cpu()
        return tensor.numpy()

import abc
import random
import numpy as np
import sklearn
import math


class LM(abc.ABC):
    @abc.abstractmethod
    def loglikelihood(self, requests):
        """Compute log-likelihood of generating a continuation from a context.
        Downstream tasks should attempt to use loglikelihood instead of other 
        LM calls whenever possible.

        :param requests: list
            A list of pairs (context, continuation)
            context: str
                Context string
            continuation: str
                The continuation over which log likelihood will be calculated. If 
                there is a word boundary, the space should be in the continuation. 
                For example, context="hello" continuation=" world" is correct.
        :return: list
            A list of pairs (logprob, isgreedy)
            logprob: float
                The log probability of `contination`
            isgreedy:
                Whether `contination` would be generated by greedy sampling from `context`
        """
        pass

    @abc.abstractmethod
    def greedy_until(self, requests):
        """Generate greedily until a stopping sequence

        :param requests: list
            A list of pairs (context, until)
            context: str
                Context string
            until: str
                The string sequence to generate until. This string sequence may 
                span across multiple tokens, or may be part of one token.
        :return: list
            A list of strings continuation
            continuation: str
                The generated continuation.
        """
        pass

    @classmethod
    def create_from_arg_string(cls, arg_string):
        """Constructor method, in case models need additional arguments
        e.g. OpenAI API engine, paths for loading, other params

        :param arg_string: str
            Left up to individual model class to handle

        """
        return cls()


class Task(abc.ABC):
    def __init__(self):
        self.download()
        self._training_docs = None

    def download(self):
        """Downloads the task dataset if necessary"""
        pass

    @abc.abstractmethod
    def has_training_docs(self):
        """Whether the task has a training set"""
        pass

    @abc.abstractmethod
    def has_validation_docs(self):
        """Whether the task has a validation set"""
        pass

    @abc.abstractmethod
    def has_test_docs(self):
        """Whether the task has a test set"""
        pass

    def training_docs(self):
        """
        :return: Iterable[obj]
            A iterable of any object, that doc_to_text can handle
        """
        return []

    def validation_docs(self):
        """
        :return: Iterable[obj]
            A iterable of any object, that doc_to_text can handle
        """
        return []

    def test_docs(self):
        """
        :return: Iterable[obj]
            A iterable of any object, that doc_to_text can handle
        """
        return []

    def fewshot_examples(self, k):
        if self._training_docs is None:
            self._training_docs = list(self.training_docs())
        return random.sample(self._training_docs, k)

    @abc.abstractmethod
    def doc_to_text(self, doc):
        pass

    @abc.abstractmethod
    def doc_to_target(self, doc):
        pass

    @abc.abstractmethod
    def construct_requests(self, doc, ctx):
        """ Uses RequestFactory to construct Requests and returns an iterable of 
        Requests which will be sent to the LM.

        :param doc:
            The document as returned from training_docs, validation_docs, or test_docs.
        :param ctx: str
            The context string, generated by fewshot_context. This includes the natural 
            language description, as well as the few shot examples, and the question
            part of the document for `doc`. 
        """
        pass

    @abc.abstractmethod
    def process_results(self, doc, results):
        """Take a single document and the LM results and evaluates, returning a 
        dict where keys are the names of submetrics and values are the values of 
        the metric for that one document

        :param doc:
            The document as returned from training_docs, validation_docs, or test_docs.
        :param results:
            The results of the requests created in construct_requests.
        """
        pass

    @abc.abstractmethod
    def aggregation(self):
        """
        :returns: {str: [float] -> float}
            A dictionary where keys are the names of submetrics and values are 
            functions that aggregate a list of metrics
        """
        pass

    @abc.abstractmethod
    def higher_is_better(self):
        """
        :returns: {str: bool}
            A dictionary where keys are the names of submetrics and values are 
            whether a higher value of the submetric is better
        """
        pass

    def fewshot_description(self):
        return ""

    def fewshot_context(self, doc, num_fewshot, provide_description):
        raw_description = self.fewshot_description()
        description = (raw_description + "\n===\n\n") if provide_description and raw_description else ""

        if num_fewshot == 0:
            labeled_examples = ""
        else:
            labeled_examples = "\n\n".join(
                [self.doc_to_text(doc) + self.doc_to_target(doc) for doc in self.fewshot_examples(k=num_fewshot)]
            ) + "\n\n"

        example = self.doc_to_text(doc)
        return description + labeled_examples + example


class MultipleChoiceTask(Task):
    def doc_to_target(self, doc):
        return " " + doc['choices'][doc['gold']]

    def construct_requests(self, doc, ctx):
        lls = [
            rf.loglikelihood(ctx, " {}".format(choice))[0]
            for choice in doc['choices']
        ]

        return lls

    def process_results(self, doc, results):
        gold = doc["gold"]

        acc = 1. if np.argmax(results) == gold else 0.

        return {
            "acc": acc
        }
    
    def higher_is_better(self):
        return {
            "acc": True
        }
    
    def aggregation(self):
        return {
            "acc": mean
        }


def mean(arr):
    return sum(arr) / len(arr)


def median(arr):
    return arr[len(arr) // 2]


def matthews_corrcoef(items):
    unzipped_list = list(zip(*items))
    golds = unzipped_list[0]
    preds = unzipped_list[1]
    return sklearn.metrics.matthews_corrcoef(golds, preds)


def f1_score(items):
    unzipped_list = list(zip(*items))
    golds = unzipped_list[0]
    preds = unzipped_list[1]
    fscore = sklearn.metrics.f1_score(golds, preds)

    return np.max(fscore)


def acc_all(items):
    # Only count as correct if all answers are labeled correctly for each question
    question_scoring_dict = {}
    preds = list(zip(*items))[0]
    docs = list(zip(*items))[1]

    for doc, pred in zip(docs, preds):
        question_id = doc["idx"]["question"]
        if question_id not in question_scoring_dict:
            question_scoring_dict[question_id] = []

        gold_label = doc["label"] == 1
        question_scoring_dict[question_id].append(gold_label == pred)
            
    acc = np.mean([int(all(x)) for x in question_scoring_dict.values()])
    return acc


def metric_max_over_ground_truths(metric_fn, prediction, ground_truths):
    """Compute max metric between prediction and each ground truth."""
    scores_for_ground_truths = []
    for ground_truth in ground_truths:
        score = metric_fn(prediction, ground_truth)
        scores_for_ground_truths.append(score)
    return max(scores_for_ground_truths)


def perplexity(items):
    return math.exp(-mean(items))

req_ret_lens = {
    'loglikelihood': 2,
}

import os
import json
import hashlib
from sqlitedict import SqliteDict

def hash_args(args):
    dat = b""
    for arg in args:
        assert isinstance(arg, str) or isinstance(arg, int)
        dat += str(arg).encode()
        dat += b"\0"
    return hashlib.sha256(dat).hexdigest()


class CachingLM:
    def __init__(self, lm, cache_db):
        self.lm = lm
        self.cache_db = cache_db
        os.makedirs(os.path.dirname(cache_db), exist_ok=True)
        self.dbdict = SqliteDict(cache_db, autocommit=True)

    def __getattr__(self, attr):
        def fn(requests):
            res = []
            remaining_reqs = []
            
            # figure out which ones are cached and which ones are new
            for req in requests:
                hsh = attr + '_' + hash_args(req)
                if hsh in self.dbdict:
                    ob = self.dbdict[hsh]

                    assert ob is not None

                    res.append(ob)
                else:
                    res.append(None)
                    remaining_reqs.append(req)
            
            # actually run the LM
            rem_res = getattr(self.lm, attr)(remaining_reqs)

            # stick the new ones back into the list and also cache any of the new ones
            resptr = 0
            for req, r in zip(remaining_reqs, rem_res):
                while res[resptr] is not None: resptr += 1

                res[resptr] = r

                # caching
                hsh = attr + '_' + hash_args(req)
                self.dbdict[hsh] = r
                

            return res
        return fn


class Request:
    def __init__(self, type, args, index=None):
        if type not in req_ret_lens.keys():
            raise NotImplementedError('The request type {} is not implemented!'.format(type))

        self.type = type
        self.args = args
        self.index = index
    
    def __iter__(self):
        i = 0
        for i in range(req_ret_lens[self.type]):
            yield Request(self.type, self.args, i)
    
    def __getitem__(self, i):
        return Request(self.type, self.args, i)


class RequestFactory:
    def __getattr__(self, attr):
        def fn(*args):
            return Request(attr, args)
        return fn


rf = RequestFactory()

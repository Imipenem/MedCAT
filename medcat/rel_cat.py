import json
import logging
from multiprocessing import Value
import os
import numpy
import logging
import torch

import torch.nn
import torch.optim
import torch
import torch.nn as nn
from datetime import date, datetime
from torch.nn.modules.module import T
from transformers import BertConfig
from medcat.cdb import CDB
from medcat.config_rel_cat import ConfigRelCAT
from medcat.pipeline.pipe_runner import PipeRunner
from medcat.utils.relation_extraction.tokenizer import TokenizerWrapperBERT

from spacy.tokens import Doc
from typing import Any, Dict, Iterable, Iterator, List, Optional, Set, Tuple, Union, cast
from transformers import AutoTokenizer
from torch.utils.data import DataLoader
from medcat.utils.meta_cat.ml_utils import predict, split_list_train_test

from medcat.utils.relation_extraction.models import BertModel_RelationExtraction
from medcat.utils.relation_extraction.pad_seq import Pad_Sequence

from medcat.utils.relation_extraction.utils import create_tokenizer_pretrain,  load_results, load_state, save_results

from medcat.utils.relation_extraction.rel_dataset import RelData
from medcat.utils.meta_cat.data_utils import Doc as FakeDoc

class RelCAT(PipeRunner):

    name: str = "rel"

    def __init__(self, cdb : CDB, config: ConfigRelCAT = ConfigRelCAT(), re_model_path: Optional[str] = "", tokenizer: Optional[TokenizerWrapperBERT] = None, task="train"):
    
       self.config = config
       self.tokenizer = tokenizer
       self.cdb = cdb
      
       self.learning_rate = config.train["lr"]
       self.batch_size = config.train["batch_size"]
       self.n_classes = config.model["nclasses"]

       self.is_cuda_available = torch.cuda.is_available()

       self.device = torch.device("cuda:0" if self.is_cuda_available else "cpu")
       self.hf_model_name = os.path.join(re_model_path, self.config.general["model_name"]) if re_model_path != "" and os.path.exists(re_model_path) else self.config.general["model_name"]
       
       self.model_config = BertConfig.from_pretrained(pretrained_model_name_or_path=self.hf_model_name, output_hidden_states=True) 

       if self.is_cuda_available:
           self.model = self.model.to(self.device)

       if self.tokenizer is None:
            tokenizer_path = os.path.join("", self.config.general["tokenizer_name"])
            if os.path.exists(tokenizer_path):
                print("Loaded tokenizer from path:", tokenizer_path)
                self.tokenizer = TokenizerWrapperBERT.load(tokenizer_path)
            else:
                self.tokenizer = TokenizerWrapperBERT(AutoTokenizer.from_pretrained(pretrained_model_name_or_path=self.hf_model_name ))
                create_tokenizer_pretrain(self.tokenizer, tokenizer_path)
    
       self.model_config.vocab_size = len(self.tokenizer.hf_tokenizers)

       self.model = BertModel_RelationExtraction.from_pretrained(pretrained_model_name_or_path=self.hf_model_name,
                                                                       model_size=self.hf_model_name,
                                                                       model_config=self.model_config,
                                                                       task=task,
                                                                       n_classes=self.n_classes,
                                                                       ignore_mismatched_sizes=True)  
       
       """
       self.model.resize_token_embeddings(self.model_config.vocab_size)
       """
       unfrozen_layers = ["classifier", "pooler", "encoder.layer.11", \
                          "classification_layer", "blanks_linear", "lm_linear", "cls"]

       for name, param in self.model.named_parameters():
        if not any([layer in name for layer in unfrozen_layers]):
            param.requires_grad = False
        else:
            param.requires_grad = True

       self.pad_id = self.tokenizer.hf_tokenizers.pad_token_id
       self.padding_seq = Pad_Sequence(seq_pad_value=self.pad_id,\
                       label_pad_value=self.pad_id,\
                       label2_pad_value=-1)

    def save(self) -> None:
        self.model_config.to_json_file("model_config.json")
        self.config

        pass

    @classmethod
    def load(cls, re_model_path: str) -> "RelCAT":
        
        tokenizer = None
        cdb = CDB() 
        config = cast(ConfigRelCAT, ConfigRelCAT.load(os.path.join(re_model_path, "config.json")))
        
        return cls(cdb=cdb, config=config, tokenizer=tokenizer, re_model_path=re_model_path, task=config.general["task"])

    def create_test_train_datasets(self, data, split_sets=False):
        train_data, test_data = {}, {}
        
        if split_sets:
            train_data["output_relations"], test_data["output_relations"] = split_list_train_test(data["output_relations"],
                            test_size=self.config.train["test_size"], shuffle=False)
        

            test_data_label_names = [rec[4] for rec in test_data["output_relations"]]
            test_data["n_classes"], test_data["unique_labels"], test_data["labels2idx"], test_data["idx2label"] = RelData.get_labels(test_data_label_names)

            for idx in range(len(test_data["output_relations"])):
                test_data["output_relations"][idx][5] = test_data["labels2idx"][test_data["output_relations"][idx][4]]
        else:
            train_data["output_relations"] = data["output_relations"]

        for k, v in data.items():
            if k != "output_relations":
                train_data[k] = []
                test_data[k] = []

        train_data_label_names = [rec[4] for rec in train_data["output_relations"]]
        train_data["n_classes"], train_data["unique_labels"], train_data["labels2idx"], train_data["idx2label"] = RelData.get_labels(train_data_label_names)

        for idx in range(len(train_data["output_relations"])):
            train_data["output_relations"][idx][5] = train_data["labels2idx"][train_data["output_relations"][idx][4]]

        return train_data, test_data

    def train(self, export_data_path = "", train_csv_path = "", test_csv_path = "", checkpoint_path="./"):
        
        train_rel_data = RelData(cdb=self.cdb, config=self.config, tokenizer=self.tokenizer)
        test_rel_data = RelData(cdb=CDB(self.cdb.config), config=self.config, tokenizer=None)

        if train_csv_path != "":
            if test_csv_path != "":
                train_rel_data.dataset, _ = self.create_test_train_datasets(train_rel_data.create_base_relations_from_csv(train_csv_path), split_sets=False)
                test_rel_data.dataset, _ = self.create_test_train_datasets(train_rel_data.create_base_relations_from_csv(test_csv_path), split_sets=False)
            else:
                train_rel_data.dataset, test_rel_data.dataset = self.create_test_train_datasets(train_rel_data.create_base_relations_from_csv(train_csv_path), split_sets=True)
          
        elif export_data_path != "":
            export_data = {}
            with open(export_data_path) as f:
                export_data = json.load(f)
            train_rel_data.dataset, test_rel_data.dataset = self.create_test_train_datasets(train_rel_data.create_relations_from_export(export_data), split_sets=True)
        else:
            raise ValueError("NO DATA HAS BEEN PROVIDED (JSON/CSV/spacy_DOCS)")

        train_dataset_size = len(train_rel_data)
        batch_size = train_dataset_size if train_dataset_size < self.batch_size else self.batch_size
        train_dataloader = DataLoader(train_rel_data, batch_size=batch_size, shuffle=False, \
                                  num_workers=0, collate_fn=self.padding_seq, pin_memory=False)

        test_dataset_size = len(test_rel_data)
        test_batch_size = test_dataset_size if test_dataset_size < self.batch_size else self.batch_size
        test_dataloader = DataLoader(test_rel_data, batch_size=test_batch_size, shuffle=False, \
                                 num_workers=0, collate_fn=self.padding_seq, pin_memory=False)

        criterion = nn.CrossEntropyLoss(ignore_index=-1)    
        optimizer = torch.optim.Adam([{"params": self.model.parameters(), "lr": self.learning_rate}])
        
        scheduler = torch.optim.lr_scheduler.MultiStepLR(optimizer, milestones=self.config.train["multistep_milestones"], gamma=self.config.train["multistep_lr_gamma"])
        
        start_epoch, best_pred = load_state(self.model, optimizer, scheduler, load_best=False, path=checkpoint_path)  

        logging.info("Starting training process...")
        
        losses_per_epoch, accuracy_per_epoch, test_f1_per_epoch = load_results(path=checkpoint_path)

        self.model.n_classes = self.config.model["nclasses"] = train_rel_data.dataset["n_classes"]
        self.config.general["labels2idx"] = train_rel_data.dataset["labels2idx"]
        gradient_acc_steps = self.config.train["gradient_acc_steps"]
        max_grad_norm = self.config.train["max_grad_norm"]

        for epoch in range(start_epoch, self.config.train["nepochs"]):
            start_time = datetime.now().time()
            self.model.train()
            
            total_loss = 0.0
            total_acc = 0.0

            loss_per_batch = []
            accuracy_per_batch = []

            logging.info("epoch %d" % epoch)
            
            for i, data in enumerate(train_dataloader, 0): 

                current_batch_size = len(data[0])

                logging.info("Processing batch %d of epoch %d , batch: %d / %d" % ( i + 1, epoch, (i + 1) * current_batch_size, train_dataset_size ))
                token_ids, e1_e2_start, labels, _, _, _ = data
                
                attention_mask = (token_ids != self.pad_id).float()
                token_type_ids = torch.zeros((token_ids.shape[0], token_ids.shape[1])).long()
                
                if self.is_cuda_available:
                    token_type_ids = token_type_ids.cuda()
                    labels = labels.cuda()
                    attention_mask = attention_mask.cuda()
                    token_type_ids = token_type_ids.cuda()

                model_output, classification_logits = self.model(
                            input_ids=token_ids,
                            token_type_ids=token_type_ids,
                            attention_mask=attention_mask,
                            e1_e2_start=e1_e2_start
                          )

                batch_loss = criterion(classification_logits.view(-1, self.model.n_classes), labels.squeeze(1))
                batch_loss = batch_loss / gradient_acc_steps
                
                total_loss += batch_loss.item() / current_batch_size

                batch_loss.backward()

                batch_acc, _, batch_precision, batch_f1, _, _ = self.evaluate_(classification_logits, labels, ignore_idx=-1)                
                total_acc += batch_acc
            
                loss_per_batch.append(batch_loss / current_batch_size)
                accuracy_per_batch.append(batch_acc)

                torch.nn.utils.clip_grad_norm_(self.model.parameters(), max_grad_norm)
                
                if (i % gradient_acc_steps) == 0:
                    optimizer.step()
                    optimizer.zero_grad()

                print('[Epoch: %d, %5d/ %d points], loss per batch, accuracy per batch: %.3f, %.3f, average total loss %.3f , total loss %.3f' %
                    (epoch, (i + 1) * current_batch_size, train_dataset_size, loss_per_batch[-1], accuracy_per_batch[-1], total_loss / (i + 1), total_loss))

            if len(loss_per_batch) > 0:
                losses_per_epoch.append(sum(loss_per_batch)/len(loss_per_batch))
                print("Losses at Epoch %d: %.5f" % (epoch, losses_per_epoch[-1]))
                
            if len(accuracy_per_batch) > 0:
                accuracy_per_epoch.append(sum(accuracy_per_batch)/len(accuracy_per_batch))
                print("Train accuracy at Epoch %d: %.5f" % (epoch, accuracy_per_epoch[-1]))

            total_loss = total_loss / (i + 1); 
            total_acc = total_acc / (i + 1)

            end_time = datetime.now().time()
            scheduler.step()

            results = self.evaluate_results(test_dataloader, self.pad_id)
                 
            test_f1_per_epoch.append(results['f1'])

            print("Test f1 at Epoch %d: %.5f" % (epoch, test_f1_per_epoch[-1]))
            print("Epoch finished, took " + str(datetime.combine(date.today(), end_time) - datetime.combine(date.today(), start_time) ) + " seconds")

            if len(accuracy_per_epoch) > 0 and accuracy_per_epoch[-1] > best_pred:
                best_pred = accuracy_per_epoch[-1]
                torch.save({
                        'epoch': epoch,\
                        'state_dict': self.model.state_dict(),\
                        'best_acc': best_pred,\
                        'optimizer' : optimizer.state_dict(),\
                        'scheduler' : scheduler.state_dict(),\
                }, os.path.join(checkpoint_path, "training_model_best_BERT.dat"))
            
            if (epoch % 1) == 0:
                save_results({ "losses_per_epoch": losses_per_epoch, "accuracy_per_epoch": accuracy_per_epoch, "f1_per_epoch" : test_f1_per_epoch}, file_prefix="train", path=checkpoint_path)
                torch.save({ 
                        'epoch': epoch,\
                        'state_dict': self.model.state_dict(),
                        'best_acc':  best_pred,  
                        'optimizer' : optimizer.state_dict(),
                        'scheduler' : scheduler.state_dict()
                    }, os.path.join(checkpoint_path, "training_checkpoint_BERT.dat" ))

    def evaluate_(self, output_logits, labels, ignore_idx):
        ### ignore index (padding) when calculating accuracy
        idxs = (labels != ignore_idx).squeeze()
        labels_ = labels.squeeze()[idxs]
        pred_labels = torch.softmax(output_logits, dim=1).max(1)[1]
        pred_labels = pred_labels[idxs]

        size_of_batch = len(idxs)

        if len(idxs) > 1:
            acc = (labels_ == pred_labels).sum().item() / size_of_batch
        else:
            acc = (labels_ == pred_labels).sum().item()

        true_labels = labels_.cpu().numpy().tolist() if labels_.is_cuda else labels_.numpy().tolist()
        pred_labels = pred_labels.cpu().numpy().tolist() if pred_labels.is_cuda else pred_labels.numpy().tolist()

        unique_labels = set(true_labels)

        stat_per_label = dict()
        
        total_tp, total_fp, total_tn, total_fn = 0, 0, 0, 0

        for label in unique_labels:
            stat_per_label[label] = {"tp": 0, "fp" : 0, "tn" : 0, "fn" : 0}
            for true_label, pred_label in zip(true_labels, pred_labels):
                if label == true_label and label == pred_label:
                    stat_per_label[label]["tp"] += 1
                    total_tp += 1
                elif label == pred_label and true_label != pred_label:
                    stat_per_label[label]["fp"] += 1
                    total_fp += 1
                if true_label == label and label != pred_label:
                    stat_per_label[label]["tn"] += 1
                    total_tn += 1
                elif true_label != label and pred_label == label:
                    stat_per_label[label]["fn"] += 1
                    total_fn += 1

        tp_fn = total_fn + total_tp
        tp_fn = tp_fn if tp_fn > 0.0 else 1.0
        tp_fp = total_fp + total_tp
        tp_fp = tp_fp if tp_fp > 0.0 else 1.0 

        recall =  total_tp / tp_fn
        precision = total_tp / tp_fp

        re_pr = recall + precision 
        re_pr = re_pr if re_pr > 0.0 else 1.0
        f1 = (2 * (recall * precision)) / re_pr

        return acc, recall, precision, f1, pred_labels, true_labels

    def evaluate_results(self, data_loader, pad_id):
        logging.info("Evaluating test samples...")
        criterion = nn.CrossEntropyLoss(ignore_index=-1)
        total_loss, total_acc, total_f1, total_recall, total_precision = 0.0, 0.0, 0.0, 0.0, 0.0
        all_true_labels = None
        all_pred_labels = None
        pred_logits = None
        
        self.model.eval()

        num_samples = len(data_loader)
      
        for i, data in enumerate(data_loader):
            with torch.no_grad():
                token_ids, e1_e2_start, labels, _, _, _ = data
                attention_mask = (token_ids != pad_id).float()
                token_type_ids = torch.zeros((token_ids.shape[0], token_ids.shape[1])).long()

                if self.is_cuda_available:
                    token_ids = token_ids.cuda()
                    labels = labels.cuda()
                    attention_mask = attention_mask.cuda()
                    token_type_ids = token_type_ids.cuda()
                    all_true_labels = all_true_labels.cuda()
                    all_pred_labels = all_pred_labels.cuda()

                model_output, pred_classification_logits = self.model(token_ids, token_type_ids=token_type_ids, attention_mask=attention_mask, Q=None,\
                            e1_e2_start=e1_e2_start)

                batch_loss = criterion(pred_classification_logits.view(-1, self.model.n_classes), labels.squeeze(1))
                total_loss += batch_loss.item()

                pred_logits = pred_classification_logits if pred_logits is None \
                     else numpy.append(pred_logits, pred_classification_logits, axis=0)

                batch_accuracy, batch_recall, batch_precision, batch_f1, pred_labels, true_labels = \
                    self.evaluate_(pred_classification_logits, labels, ignore_idx=-1)
                
                pred_labels = torch.tensor(pred_labels)
                true_labels = torch.tensor(true_labels)

                all_true_labels = true_labels if all_true_labels is None else torch.cat((all_true_labels, true_labels))
                all_pred_labels = pred_labels if all_pred_labels is None else torch.cat((all_pred_labels, pred_labels))

                total_acc += batch_accuracy
                total_recall += batch_recall
                total_precision += batch_precision
                total_f1 += batch_f1

        total_loss = total_loss / (i + 1)
        total_acc = total_acc / (i + 1)
        total_precision = total_precision / (i + 1)
        total_f1 = total_f1 / (i + 1)
        total_recall = total_recall / (i + 1)

        results = {
            "loss" : total_loss,
            "accuracy": total_acc,
            "precision": total_precision,
            "recall": total_recall,
            "f1": total_f1
        }

        logging.info("***** Eval results *****")
        for key in sorted(results.keys()):
            logging.info("  %s = %s", key, str(results[key]))
        
        return results

    def predict(self, docs: Iterable[Doc]) -> Any:

        predict_rel_dataset = RelData(cdb=self.cdb, config=self.config, tokenizer=self.tokenizer)
        #predict_rel_dataset.dataset = DataLoader(predict_rel_dataset.generate_base_relations(docs), shuffle=False, \
        #                          num_workers=0, collate_fn=self.padding_seq, pin_memory=False)
        
        output = predict_rel_dataset.generate_base_relations(docs)

        for i, doc_relations in enumerate(output, 0):
            relation_instances = doc_relations["output_relations"]
            
            for rel_data in relation_instances:
                token_ids, e1_e2_start, label, label_id, _, _, _, _, _, _, _, _, doc_id = rel_data
                
                token_ids = torch.LongTensor(token_ids).unsqueeze(0)
                e1_e2_start = torch.LongTensor(e1_e2_start).unsqueeze(0)
    
                attention_mask = (token_ids != self.pad_id).float()
                token_type_ids = torch.zeros(token_ids.shape[0], token_ids.shape[1]).long()
            
                if self.is_cuda_available:
                    token_ids = token_ids.cuda()
                    attention_mask = attention_mask.cuda()
                    token_type_ids = token_type_ids.cuda()

                with torch.no_grad():
                    classification_logits = self.model(token_ids, token_type_ids=token_type_ids, attention_mask=attention_mask, Q=None,\
                                        e1_e2_start=e1_e2_start)

        print("Predicted : ")
        pass

    def pipe(self, stream: Iterable[Union[Doc, FakeDoc]], *args, **kwargs) -> Iterator[Doc]:
        
        pass
    
    def __call__(self, doc: Doc) -> Doc:
        doc = next(self.pipe(iter([doc])))
        return doc
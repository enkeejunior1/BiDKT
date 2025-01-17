import torch
from copy import deepcopy

from torch.nn.functional import one_hot
from sklearn import metrics
import numpy as np
from tqdm import tqdm
from random import random, randint

from utils import EarlyStopping

# 15% <MASK>
def Mlm4BertTrain(r_seqs, mask_seqs):
    #|r_seqs| = (bs, n)

    # r_seqs를 불러들인 후, for문으로 반복해서 불러들임
    mlm_r_seqs = []
    mlm_idxs = []

    # 불러들인 r_seq를 대상으로 torch.masked_select()를 사용해서 길이를 자름
    # PAD는 -1로 처리
    for r_seq, mask_seq in zip(r_seqs, mask_seqs):
        r_len = r_seq.size(0)
        # real_r_seq: r_seq에 <PAD>를 제거한 sq
        real_r_seq = torch.masked_select(r_seq, mask_seq).cpu()
        real_r_seq_len = real_r_seq.size(0)

        mlm_idx = np.random.choice(real_r_seq_len, int(real_r_seq_len*0.15), replace=False)

        for idx in mlm_idx:
            if random() < 0.8: # 15% 중 80%는 <MASK>
                real_r_seq[idx] = 2 # <MASK>는 2로 표시하기
            elif random() < 0.5: # 15% 중 10%는 0과 1의 random 값으로 넣기
                real_r_seq[idx] = randint(0, 1)
            # 15% 중 10%는 원래 값 그대로

        # pad_r_seq에 PAD(-1)을 씌움
        pad_len = r_len - real_r_seq_len
        # <PAD>를 3으로 채우기, -1로 채우면 embedding에서 error 발생
        pad_seq = torch.full((1, pad_len), 3).squeeze(0) 
        # 패드 다시 결합
        pad_r_seq = torch.cat((real_r_seq, pad_seq), dim=-1)
        # mlm_r_seqs에 넣기
        mlm_r_seqs.append(pad_r_seq)

        # <mask> idx bool 만들기
        # r_len의 길이(전체 길이)만큼 zero vector로 만듦
        mlm_zeros = np.zeros(shape=(r_len, ))
        # mlm_idx에 해당하는 곳에 1을 넣음
        mlm_zeros[mlm_idx] = 1
        # mlm_idxs에 값을 더함
        mlm_idxs.append(mlm_zeros)

    mlm_r_seqs = torch.stack(mlm_r_seqs)
    mlm_idxs = torch.BoolTensor(mlm_idxs)

    # mlm_r_seqs: mask가 씌워진 r_seqs
    # mlm_idx: mask가 씌워진 idx 값
    return mlm_r_seqs, mlm_idxs
    # |mlm_r_seqs| = (bs, n)
    # |mask_seqs| = (bs, n)

# seq의 가장 마지막에 <MASK>
def Mlm4BertTest(r_seqs, mask_seqs):
    #|r_seqs| = (bs, n)

    mlm_r_seqs = []
    mlm_idxs = []

    for r_seq, mask_seq in zip(r_seqs, mask_seqs):
        r_len = r_seq.size(0)

        real_r_seq = torch.masked_select(r_seq, mask_seq).cpu()
        real_r_seq_len = real_r_seq.size(0)

        #여기서는 real_r_seq의 마지막
        mlm_idx = real_r_seq_len - 1
        #마지막 index에 <MASK>를 씌움, <MASK>는 2
        real_r_seq[mlm_idx] = 2

        pad_len = r_len - real_r_seq_len
        pad_seq = torch.full((1, pad_len), 3).squeeze(0) # <PAD>는 3
        pad_r_seq = torch.cat((real_r_seq, pad_seq), dim=-1)
        mlm_r_seqs.append(pad_r_seq)

        mlm_zeros = np.zeros(shape=(r_len, ))
        mlm_zeros[mlm_idx] = 1
        mlm_idxs.append(mlm_zeros)

    mlm_r_seqs = torch.stack(mlm_r_seqs)
    mlm_idxs = torch.BoolTensor(mlm_idxs)

    return mlm_r_seqs, mlm_idxs
    # |mlm_r_seqs| = (bs, n)
    # |mask_seqs| = (bs, n)

class BidktTrainer():

    def __init__(
        self, 
        model, 
        optimizer, 
        n_epochs, 
        device, 
        num_q, 
        crit, 
        max_seq_len, 
        grad_acc=False, 
        grad_acc_iter=4 #4면 기존 batch_size의 4배
        ):
        self.model = model
        self.optimizer = optimizer
        self.n_epochs = n_epochs
        self.device = device
        self.num_q = num_q
        self.crit = crit
        self.max_seq_len = max_seq_len
        self.grad_acc = grad_acc #gradient accumulation
        self.grad_acc_iter = grad_acc_iter
    
    def _train(self, train_loader, metric_name):

        auc_score = 0
        y_trues, y_scores = [], []
        loss_list = []

        for idx, data in enumerate(tqdm(train_loader)):
            self.model.train()
            q_seqs, r_seqs, mask_seqs = data

            q_seqs = q_seqs.to(self.device) #|q_seqs| = (bs, n)
            r_seqs = r_seqs.to(self.device) #|r_seqs| = (bs, n)
            mask_seqs = mask_seqs.to(self.device) #|mask_seqs| = (bs, n)

            # correct에서 따로 사용하기 위해 clone 작성
            real_seqs = r_seqs.clone()

            # mlm_r_seqs: r_seqs에 Masked Language Model 구현을 위한 [MASK]를 씌움, [MASK]는 2로 표기 / mlm_idx: [MASK]의 위치
            mlm_r_seqs, mlm_idxs = Mlm4BertTrain(r_seqs, mask_seqs)
            # |mlm_r_seqs| = (bs, n)
            # |mlm_idxs| = (bs, n), True or False가 들어있어야 함

            mlm_r_seqs = mlm_r_seqs.to(self.device)
            mlm_idxs = mlm_idxs.to(self.device)

            # zero_grad(아래로 위치 변경)
        
            y_hat = self.model(
                q_seqs.long(), 
                mlm_r_seqs.long(), # train을 위한 mlm된 r_seqs
                mask_seqs.long() # attn_mask
            ).to(self.device)
            # |y_hat| = (bs, n, output_size=1)

            y_hat = y_hat.squeeze()
             # |y_hat| = (bs, n)

            # 예측값과 실제값
            y_hat = torch.masked_select(y_hat, mlm_idxs)
            #|y_hat| = (bs * n - n_mlm_idxs)
            correct = torch.masked_select(real_seqs, mlm_idxs)
            #|correct| = (bs * n - n_mlm_idxs)

            loss = self.crit(y_hat, correct)
            # |loss| = (1)

            #grad_accumulation
            if self.grad_acc == True:
                loss.backward()
                if (idx + 1) % self.grad_acc_iter == 0:
                    self.optimizer.step()
                    self.optimizer.zero_grad()
            else:
                self.optimizer.zero_grad()
                loss.backward()
                self.optimizer.step()

            y_trues.append(correct)
            y_scores.append(y_hat)
            loss_list.append(loss)

        y_trues = torch.cat(y_trues).detach().cpu().numpy()
        y_scores = torch.cat(y_scores).detach().cpu().numpy()

        auc_score += metrics.roc_auc_score( y_trues, y_scores )

        loss_result = torch.mean(torch.Tensor(loss_list)).detach().cpu().numpy()

        if metric_name == "AUC":
            return auc_score
        elif metric_name == "RMSE":
            return loss_result

    def _validate(self, valid_loader, metric_name):

        auc_score = 0
        y_trues, y_scores = [], []
        loss_list = []

        with torch.no_grad():
            for data in tqdm(valid_loader):
                self.model.eval()
                q_seqs, r_seqs, mask_seqs = data #collate에 정의된 데이터가 나옴
                #|r_seqs| = (bs, sq)
                
                q_seqs = q_seqs.to(self.device)
                r_seqs = r_seqs.to(self.device)
                mask_seqs = mask_seqs.to(self.device)

                real_seqs = r_seqs.clone()

                mlm_r_seqs, mlm_idxs = Mlm4BertTest(r_seqs, mask_seqs)

                mlm_r_seqs = mlm_r_seqs.to(self.device)
                mlm_idxs = mlm_idxs.to(self.device)
                # |mlm_r_seqs| = (bs, n)
                # |mlm_idxs| = (bs, n), True or False가 들어있어야 함

                y_hat = self.model(
                    q_seqs.long(),
                    mlm_r_seqs.long(),
                    mask_seqs.long()
                ).to(self.device)

                y_hat = y_hat.squeeze()

                y_hat = torch.masked_select(y_hat, mlm_idxs)
                correct = torch.masked_select(real_seqs, mlm_idxs)

                loss = self.crit(y_hat, correct)
                # |loss| = (1)

                y_trues.append(correct)
                y_scores.append(y_hat)
                loss_list.append(loss)

        y_trues = torch.cat(y_trues).detach().cpu().numpy()
        y_scores = torch.cat(y_scores).detach().cpu().numpy()

        auc_score += metrics.roc_auc_score( y_trues, y_scores )

        loss_result = torch.mean(torch.Tensor(loss_list)).detach().cpu().numpy()

        if metric_name == "AUC":
            return auc_score
        elif metric_name == "RMSE":
            return loss_result

    def _test(self, test_loader, metric_name):

        auc_score = 0
        y_trues, y_scores = [], []
        loss_list = []

        with torch.no_grad():
            for data in tqdm(test_loader):
                self.model.eval()
                q_seqs, r_seqs, mask_seqs = data #collate에 정의된 데이터가 나옴
                #|r_seqs| = (bs, sq)
                
                q_seqs = q_seqs.to(self.device)
                r_seqs = r_seqs.to(self.device)
                mask_seqs = mask_seqs.to(self.device)

                real_seqs = r_seqs.clone()

                mlm_r_seqs, mlm_idxs = Mlm4BertTest(r_seqs, mask_seqs)

                mlm_r_seqs = mlm_r_seqs.to(self.device)
                mlm_idxs = mlm_idxs.to(self.device)
                # |mlm_r_seqs| = (bs, n)
                # |mlm_idxs| = (bs, n), True or False가 들어있어야 함

                y_hat = self.model(
                    q_seqs.long(),
                    mlm_r_seqs.long(),
                    mask_seqs.long()
                ).to(self.device)

                y_hat = y_hat.squeeze()

                y_hat = torch.masked_select(y_hat, mlm_idxs)
                correct = torch.masked_select(real_seqs, mlm_idxs)

                loss = self.crit(y_hat, correct)
                # |loss| = (1)

                y_trues.append(correct)
                y_scores.append(y_hat)
                loss_list.append(loss)

        y_trues = torch.cat(y_trues).detach().cpu().numpy()
        y_scores = torch.cat(y_scores).detach().cpu().numpy()

        auc_score += metrics.roc_auc_score( y_trues, y_scores )

        loss_result = torch.mean(torch.Tensor(loss_list)).detach().cpu().numpy()

        if metric_name == "AUC":
            return auc_score
        elif metric_name == "RMSE":
            return loss_result

    #auc용으로 train
    def train(self, train_loader, valid_loader, test_loader, config):
        
        if config.crit == "binary_cross_entropy":
            best_valid_score = 0
            best_test_score = 0
            metric_name = "AUC"
        elif config.crit == "rmse":
            best_valid_score = float('inf')
            best_test_score = float('inf')
            metric_name = "RMSE"
        
        #출력을 위한 기록용
        train_scores = []
        valid_scores = []
        test_scores = []
        #best_model = None

        # early_stopping 선언
        early_stopping = EarlyStopping(metric_name=metric_name,
                                    best_score=best_valid_score)

        # Train and Valid Session
        for epoch_index in range(self.n_epochs):
            
            print("Epoch(%d/%d) start" % (
                epoch_index + 1,
                self.n_epochs
            ))

            # Training Session
            train_score = self._train(train_loader, metric_name)
            valid_score = self._validate(valid_loader, metric_name)
            test_score = self._test(test_loader, metric_name)

            # train, test record 저장
            train_scores.append(train_score)
            valid_scores.append(valid_score)
            test_scores.append(test_score)

            # early stop
            train_scores_avg = np.average(train_scores)
            valid_scores_avg = np.average(valid_scores)
            early_stopping(valid_scores_avg, self.model)
            if early_stopping.early_stop:
                print("Early stopping")
                break

            if config.crit == "binary_cross_entropy":
                if test_score >= best_test_score:
                    best_test_score = test_score
                    #best_model = deepcopy(self.model.state_dict())
            elif config.crit == "rmse":
                if test_score <= best_test_score:
                    best_test_score = test_score
                    #best_model = deepcopy(self.model.state_dict())

            print("Epoch(%d/%d) result: train_score=%.4f  valid_score=%.4f test_score=%.4f best_test_score=%.4f" % (
                epoch_index + 1,
                self.n_epochs,
                train_score,
                valid_score,
                test_score,
                best_test_score,
            ))

        print("\n")
        print("The Best Test Score(" + metric_name + ") in Testing Session is %.4f" % (
                best_test_score,
            ))
        print("\n")
        
        # 가장 최고의 모델 복구
        #self.model.load_state_dict(best_model)
        self.model.load_state_dict(torch.load("../checkpoints/checkpoint.pt"))

        return train_scores, valid_scores, \
            best_valid_score, best_test_score
# AUTOGENERATED! DO NOT EDIT! File to edit: nbs/models_mqcnn__mqcnn.ipynb (unless otherwise specified).

__all__ = ['MQCNN']

# Cell
import os
import random
import time
from collections import defaultdict
from copy import deepcopy

import numpy as np
import pandas as pd

import torch as t
import torch.nn as nn
from torch import optim

from ...data.tsdataset import TimeSeriesDataset
from ...data.tsloader_general import TimeSeriesLoader

from ..components.expanders import fExpander, yExpanderSP1
from ..components.expanders import list_ltsp as ltsps, sp1_to_spN_matrix

from ..components.common import (
    CausalConv1d, L1Regularizer,
    RepeatVector
)

from ..components.common import TimeDistributed2d as TimeDistributed
from ..components.common import TimeDistributed3d

from ...losses.pytorch import MQLoss
from ...losses.numpy import mqloss

# Cell
class _sEncoder(nn.Module):
    def __init__(self, in_features, out_features, n_time_in):
        super(_sEncoder, self).__init__()
        layers = [nn.Dropout(p=0.5),
                  nn.Linear(in_features=in_features, out_features=out_features),
                  nn.ReLU()]
        self.encoder = nn.Sequential(*layers)
        self.repeat = RepeatVector(repeats=n_time_in)

    def forward(self, x):
        # Encode and repeat values to match time
        x = self.encoder(x)
        x = self.repeat(x) # [N,S_out] -> [N,S_out,T]
        return x

class _xEncoder(nn.Module):
    def __init__(self, in_channels, out_channels, kernel_size):
        super(_xEncoder, self).__init__()
        self.l1 = TimeDistributed(L1Regularizer(in_features=in_channels,
                                                l1_lambda=1e-3))
        # Causal 1d convs with causal padding
        layers = [CausalConv1d(in_channels=in_channels, out_channels=out_channels,
                               kernel_size=kernel_size, padding=(kernel_size-1)*1,
                               activation='ReLU', dilation=1),
                  CausalConv1d(in_channels=out_channels, out_channels=out_channels,
                               kernel_size=kernel_size, padding=(kernel_size-1)*2,
                               activation='ReLU', dilation=2),
                  CausalConv1d(in_channels=out_channels, out_channels=out_channels,
                               kernel_size=kernel_size, padding=(kernel_size-1)*4,
                               activation='ReLU', dilation=4),
                  CausalConv1d(in_channels=out_channels, out_channels=out_channels,
                               kernel_size=kernel_size, padding=(kernel_size-1)*8,
                               activation='ReLU', dilation=8),
                  CausalConv1d(in_channels=out_channels, out_channels=out_channels,
                               kernel_size=kernel_size, padding=(kernel_size-1)*16,
                               activation='ReLU', dilation=16),
                  CausalConv1d(in_channels=out_channels, out_channels=out_channels,
                               kernel_size=kernel_size, padding=(kernel_size-1)*32,
                               activation='ReLU', dilation=32)]
        self.encoder = nn.Sequential(*layers)

    def forward(self, x):
        x = self.l1(x)
        x = self.encoder(x) # [N,X,T] -> [N,X_out,T]
        return x

class _fEncoder(nn.Module):
    def __init__(self, in_channels, out_channels, n_time_in, n_time_out):
        super(_fEncoder, self).__init__()
        self.in_channels = in_channels
        self.n_time_out = n_time_out
        self.fExpander = fExpander(n_time_in=n_time_in,
                                   n_time_out=n_time_out)
        layers = [TimeDistributed(nn.Linear(in_features=in_channels,
                                            out_features=out_channels)),
                  nn.ReLU()]
        self.encoder = nn.Sequential(*layers)

    def forward(self, x):
        N, C, T = x.size()
        x_expd = self.fExpander(x) # [N,F,T] -> [N,L,F,T]
        x_enc = x_expd.view(N, self.n_time_out * C, -1) # [N,L,F,T] -> [N,(LxF),T]
        x_enc = self.encoder(x_enc) # [N,F_out,T]
        return x_enc, x_expd

# Cell
class _sContext(nn.Module):
    def __init__(self, in_features, out_features, n_time_in, n_time_out):
        super(_sContext, self).__init__()
        self.n_time_in = n_time_in
        self.n_time_out = n_time_out
        self.out_features = out_features
        self.linear = TimeDistributed(nn.Linear(in_features=in_features,
                                                out_features=out_features * n_time_out))

    def forward(self, x):
        N, C, T = x.size()
        x = self.linear(x)
        x = x.view(N, self.n_time_out,
                   self.out_features, T) # [N,(LxsC_out),T] -> [N,L,sC_out,T]
        return x

class _aContext(nn.Module):
    def __init__(self, in_features, out_features, n_time_out):
        super(_aContext, self).__init__()
        self.n_time_out = n_time_out
        self.linear = TimeDistributed(nn.Linear(in_features=in_features,
                                                out_features=out_features))

    def forward(self, x):
        x = self.linear(x)
        # [N,(aC_out),T] -> [N,L,aC_out,T]
        x = x.unsqueeze(1).repeat(1, self.n_time_out, 1, 1)
        return x

# Cell
class _LocalMLP(nn.Module):
    def __init__(self, in_features, out_features):
        super(_LocalMLP, self).__init__()
        layers = [TimeDistributed3d(nn.Linear(in_features=in_features, out_features=60)),
                  nn.ReLU(),
                  TimeDistributed3d(nn.Linear(in_features=60, out_features=50)),
                  nn.ReLU(),
                  TimeDistributed3d(nn.Linear(in_features=50, out_features=out_features)),
                  nn.ReLU()]
        self.decoder = nn.Sequential(*layers)

    def forward(self, x):
        x = self.decoder(x)
        return x

class _sp1Decoder(nn.Module):
    def __init__(self, in_features, n_mq):
        super(_sp1Decoder, self).__init__()
        self.adapter = TimeDistributed3d(nn.Linear(in_features=in_features,
                                                   out_features=n_mq))

    def forward(self, x):
        x = self.adapter(x)
        x = x.permute(0,1,3,2).contiguous() #[N,L,C,T] --> [N,L,T,C]
        return x

# Cell
class _MQCNN(nn.Module):
    """ Multi Quantile Convolutional Neural Network
    """
    def __init__(self,
                 # Architecture parameters
                 n_time_in,
                 n_time_out,
                 n_s,
                 n_x,
                 n_f,
                 n_s_hidden,
                 n_x_hidden,
                 n_f_hidden,
                 n_sc_hidden,
                 n_ac_hidden,
                 n_ld_hidden,
                 n_mq):
        super(_MQCNN, self).__init__()

        self.n_time_out = n_time_out

        #---------------------- Encoders ----------------------#
        self.sencoder = _sEncoder(in_features=n_s,
                                  out_features=n_s_hidden,
                                  n_time_in=n_time_in)
        self.xencoder = _xEncoder(in_channels=n_x,
                                  out_channels=n_x_hidden,
                                  kernel_size=2)
        self.fencoder = _fEncoder(in_channels=n_time_out*n_f,
                                  out_channels=n_f_hidden,
                                  n_time_in=n_time_in,
                                  n_time_out=n_time_out)

        #---------------------- Contexts ----------------------#
        n_g_hidden = (n_s_hidden + n_x_hidden + n_f_hidden)
        self.scontext = _sContext(in_features=n_g_hidden,
                                  out_features=n_sc_hidden,
                                  n_time_in=n_time_in,
                                  n_time_out=n_time_out)
        self.acontext = _aContext(in_features=n_g_hidden,
                                  out_features=n_ac_hidden,
                                  n_time_out=n_time_out)

        #---------------------- Decoder ----------------------#
        n_ld_inputs = (n_sc_hidden + n_ac_hidden + n_f)
        self.ldecoder = _LocalMLP(in_features=n_ld_inputs,
                                  out_features=n_ld_hidden)
        self.sp1adapter = _sp1Decoder(in_features=n_ld_hidden,
                                      n_mq=n_mq)
        self.yexpandersp1 = yExpanderSP1(n_time_out=n_time_out)

    def forward(self, S, Y, X, F):
        #------------ Encoders ------------#
        hs = self.sencoder(S)
        hx = self.xencoder(X)
        hf, F_expd = self.fencoder(F)
        hg = t.cat((hs, hx, hf), axis=1) # Global codes

        #------------- Contexts -----------#
        sc = self.scontext(hg)
        ac = self.acontext(hg)
        h = t.cat((sc, ac, F_expd), axis=2) # Local codes

        #------------ Decoders ------------#
        h = self.ldecoder(h)
        y_hat = self.sp1adapter(h)

        #------------ Fork Seq ------------#
        # separate outsample_y to forecast
        y_true = self.yexpandersp1(Y)[:,:,1:-self.n_time_out+1]

        return y_true, y_hat

# Cell
class MQCNN(object):
    def __init__(self,
                 # Architecture parameters
                 n_time_in,
                 n_time_out,
                 n_s,
                 n_x,
                 n_f,
                 n_s_hidden,
                 n_x_hidden,
                 n_f_hidden,
                 n_sc_hidden,
                 n_ac_hidden,
                 n_ld_hidden,
                 # Optimization and regularization
                 n_iterations,
                 batch_size,
                 lr,
                 lr_decay,
                 lr_decay_step_size,
                 early_stop_patience,
                 training_percentiles,
                 # Data parameters
                 random_seed):

        #------------------------ Model Attributes ------------------------#
        # Architecture parameters
        self.n_time_in = n_time_in
        self.n_time_out = n_time_out

        # Optimization and regularization
        self.n_iterations = n_iterations
        self.batch_size = batch_size
        self.lr = lr
        self.lr_decay = lr_decay
        self.lr_decay_step_size = lr_decay_step_size
        self.early_stop_patience = early_stop_patience
        self.n_mq = len(training_percentiles)
        self.training_percentiles = training_percentiles

        # Data parameters
        self.random_seed = random_seed

        # Check if gpu is available
        self.device = 'cuda' if t.cuda.is_available() else 'cpu'

        #----------------------- Instantiate  Model -----------------------#
        self.model = _MQCNN(n_time_in=n_time_in,
                            n_time_out=n_time_out,
                            n_s=n_s,
                            n_x=n_x,
                            n_f=n_f,
                            n_s_hidden=n_s_hidden,
                            n_x_hidden=n_x_hidden,
                            n_f_hidden=n_f_hidden,
                            n_sc_hidden=n_sc_hidden,
                            n_ac_hidden=n_ac_hidden,
                            n_ld_hidden=n_ld_hidden,
                            n_mq=self.n_mq)

        self.model = self.model.to(self.device)

    def to_tensor(self, x: np.ndarray) -> t.Tensor:
        tensor = t.as_tensor(x, dtype=t.float32).to(self.device)
        return tensor

    def __loss_fn(self, loss_name: str):
        #TODO: replace with kwargs
        def loss(forecast, target, mask):
            if loss_name == 'MQ':
                quantiles = [tau / 100 for tau in self.training_percentiles]
                quantiles = self.to_tensor(np.array(quantiles))
                return MQLoss(y=target, y_hat=forecast, quantiles=quantiles, mask=mask)
        return loss

    def __val_loss_fn(self, loss_name):
        def loss(forecast, target, weights):
            if loss_name == 'MQ':
                quantiles = [tau / 100 for tau in self.training_percentiles]
                quantiles = np.array(quantiles)
                return mqloss(y=target, y_hat=forecast, quantiles=quantiles, weights=weights)
        return loss

    def fit(self, train_loader, val_loader=None, n_iterations=None, verbose=True, eval_freq=1):
        # TODO: Indexes hardcoded, information duplicated in train and val datasets
        assert (self.n_time_in)==train_loader.input_size, \
            f'model input_size {self.n_time_in} data input_size {train_loader.input_size}'

        # Random Seeds (model initialization)
        t.manual_seed(self.random_seed)
        np.random.seed(self.random_seed)
        random.seed(self.random_seed)

        # Overwrite n_iterations and train datasets
        if n_iterations is None:
            n_iterations = self.n_iterations

        optimizer = optim.Adam(self.model.parameters(), lr=self.lr)
        lr_scheduler = optim.lr_scheduler.StepLR(optimizer, step_size=self.lr_decay_step_size, gamma=self.lr_decay)
        training_loss_fn = self.__loss_fn(loss_name='MQ')
        validation_loss_fn = self.__val_loss_fn(loss_name='MQ') #Uses numpy losses

        if verbose:
            print('\n')
            print('='*30+' Start fitting '+'='*30)

        start = time.time()
        self.trajectories = {'iteration':[],'train_loss':[], 'val_loss':[]}
        self.final_insample_loss = None
        self.final_outsample_loss = None

        # Training Loop
        early_stopping_counter = 0
        best_val_loss = np.inf
        best_insample_loss = np.inf
        best_state_dict = deepcopy(self.model.state_dict())
        break_flag = False
        iteration = 0
        epoch = 0
        while (iteration < n_iterations) and (not break_flag):
            epoch +=1
            for batch in iter(train_loader):
                iteration += 1
                if (iteration > n_iterations) or (break_flag):
                    continue

                self.model.train()
                # Parse batch
                S     = self.to_tensor(batch['S'])
                Y     = self.to_tensor(batch['Y'])#[:, :-self.n_time_out]     #CHECAR BIEN no desfasar
                X     = self.to_tensor(batch['X'])[:, :, :-self.n_time_out]  #REVISAR VS [:-self.n_time_out]
                F     = self.to_tensor(batch['F'])
                #available_mask  = self.to_tensor(batch['available_mask'])
                #outsample_mask = self.to_tensor(batch['sample_mask'])[:, -self.n_time_out:]

                #print("\n")
                #print("Y[0,:]", Y[0,:])
                #print("\n")
                #print("X[0,:,:]", X[0,:,:])
                #print("\n")
                #print("F[0,:,:]", F[0,:,:])
                #print("\n")
                #assert 1<0

                optimizer.zero_grad()
                outsample_y, forecast = self.model(S=S, Y=Y, X=X, F=F)

                training_loss = training_loss_fn(forecast=forecast,
                                                 target=outsample_y,
                                                 mask=None)

                # Protection to exploding gradients
                if not np.isnan(float(training_loss)):
                    training_loss.backward()
                    t.nn.utils.clip_grad_norm_(self.model.parameters(), 1.0)
                    optimizer.step()
                else:
                    early_stopping_counter = self.early_stop_patience

                lr_scheduler.step()
                if (iteration % eval_freq == 0):
                    display_string = 'Step: {}, Time: {:03.3f}, Insample {}: {:.5f}'.format(iteration,
                                                                                            time.time()-start,
                                                                                            'MQLoss',
                                                                                            training_loss.cpu().data.numpy())
                    self.trajectories['iteration'].append(iteration)
                    self.trajectories['train_loss'].append(float(training_loss.cpu().data.numpy()))

                    if val_loader is not None:
                        loss = self.evaluate_performance(ts_loader=val_loader,
                                                         validation_loss_fn=validation_loss_fn)
                        display_string += ", Outsample {}: {:.5f}".format('MQLoss', loss)
                        self.trajectories['val_loss'].append(loss)

                        if self.early_stop_patience:
                            if loss < best_val_loss:
                                # Save current model if improves outsample loss
                                best_state_dict = deepcopy(self.model.state_dict())
                                best_insample_loss = training_loss.cpu().data.numpy()
                                early_stopping_counter = 0
                                best_val_loss = loss
                            else:
                                early_stopping_counter += 1
                            if early_stopping_counter >= self.early_stop_patience:
                                break_flag = True

                    print(display_string)

                if break_flag:
                    print('\n')
                    print(19*'-',' Stopped training by early stopping', 19*'-')
                    self.model.load_state_dict(best_state_dict)
                    break

        #End of fitting
        if n_iterations > 0:
            # This is batch loss!
            self.final_insample_loss = float(training_loss.cpu().data.numpy()) if not break_flag else best_insample_loss
            string = 'Step: {}, Time: {:03.3f}, Insample {}: {:.5f}'.format(iteration,
                                                                            time.time()-start,
                                                                            'MQLoss',
                                                                            self.final_insample_loss)
            if val_loader is not None:
                self.final_outsample_loss = self.evaluate_performance(ts_loader=val_loader,
                                                                      validation_loss_fn=validation_loss_fn)
                string += ", Outsample {}: {:.5f}".format('MQLoss', self.final_outsample_loss)
            print(string)
            print('='*30+'  End fitting  '+'='*30)
            print('\n')

    def predict(self, ts_loader):
        self.model.eval()
        assert not ts_loader.shuffle, 'ts_loader must have shuffle as False.'

        forecasts = []
        outsample_ys = []

        with t.no_grad():
            outsample_ys = []
            forecasts = []
            for batch in iter(ts_loader):
                # Parse batch
                S     = self.to_tensor(batch['S'])
                Y     = self.to_tensor(batch['Y'])#[:, :-self.n_time_out]
                X     = self.to_tensor(batch['X'])[:, :, :-self.n_time_out]
                F     = self.to_tensor(batch['F'])
                outsample_y, forecast = self.model(S=S, Y=Y, X=X, F=F)

                # Parsing rolled forecasts,
                # keeping only last available forecast per window
                #[728, 24, 168]
                outsample_y = outsample_y[:, :, -1]
                forecast = forecast[:, :, -1, :]

                outsample_ys.append(outsample_y.cpu().data.numpy())
                forecasts.append(forecast.cpu().data.numpy())

        forecasts = np.vstack(forecasts)
        outsample_ys = np.vstack(outsample_ys)
        outsample_masks = np.ones(outsample_ys.shape)

        self.model.train()

        return outsample_ys, forecasts, outsample_masks

    def evaluate_performance(self, ts_loader, validation_loss_fn):
        self.model.eval()

        target, forecast, outsample_mask = self.predict(ts_loader=ts_loader)

        #complete_loss = validation_loss_fn(target=target, forecast=forecast, weights=outsample_mask)
        complete_loss = validation_loss_fn(target=target, forecast=forecast, weights=None)

        self.model.train()

        return complete_loss
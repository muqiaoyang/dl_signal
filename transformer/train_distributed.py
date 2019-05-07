import torch
from torch import nn
import sys
from Dataset import SignalDataset_iq
import argparse
from model import *
import torch.optim as optim
import numpy as np
import time
from torch.optim.lr_scheduler import ReduceLROnPlateau
from torch.utils.data import DataLoader
import os
import time

from sklearn.metrics import classification_report
from sklearn.metrics import confusion_matrix
from sklearn.metrics import precision_recall_fscore_support
from sklearn.metrics import accuracy_score, f1_score
from sklearn.metrics import average_precision_score

def count_parameters(model):
    return sum(p.numel() for p in model.parameters() if p.requires_grad)

def train_transformer():
    model = TransformerModel(ntokens=10000,        # TODO: wait for Paul's data
                             time_step=args.time_step,
                             input_dims=args.modal_lengths,
                             hidden_size=args.hidden_size,
                             output_dim=args.output_dim,
                             num_heads=args.num_heads,
                             attn_dropout=args.attn_dropout,
                             relu_dropout=args.relu_dropout,
                             res_dropout=args.res_dropout,
                             layers=args.nlevels,
                             horizons=args.nhorizons,
                             attn_mask=args.attn_mask,
                             crossmodal=args.crossmodal)
    if use_cuda:
        model = model.cuda()

    print("Model size: {0}".format(count_parameters(model)))

    optimizer = getattr(optim, args.optim)(model.parameters(), lr=args.lr, weight_decay=1e-7)
    criterion = nn.BCELoss()
    scheduler = ReduceLROnPlateau(optimizer, mode='min', patience=10, factor=0.5, verbose=True)
    settings = {'model': model,
                'optimizer': optimizer,
                'criterion': criterion,
                'scheduler': scheduler}
    return train_model(settings)


def train_model(settings):
    model = settings['model']
    optimizer = settings['optimizer']
    criterion = settings['criterion']
    scheduler = settings['scheduler']
    # Initialize distributed
    # model = nn.parallel.distributed.DistributedDataParallel(model)
    model = nn.DataParallel(model)
    model.to(device)
    def train(model, optimizer, criterion):
        epoch_loss = 0.0
        num_batches = len(training_set) // args.batch_size
        total_batch_size = 0
        total_aps = 0.0
        model.train()
        start_time = time.time()
        for i_batch, (batch_X, batch_y) in enumerate(train_loader):
            cur_batch_size = len(batch_X) 
            model.zero_grad()
            # For distributed
            #with torch.cuda.device(0):
            #        batch_X = batch_X.float().cuda()
            #        batch_y = batch_y.float().cuda()
            batch_X, batch_y = batch_X.float().to(device=device), batch_y.float().to(device=device)
            batch_X = batch_X.transpose(0, 1).float()
            preds, _ = model(batch_X)
            preds = preds.transpose(0, 1)
            # reshape batch_y and preds to be of size (N, feature_dim) for loss calculation
            batch_y = batch_y.reshape(-1, batch_y.shape[-1])
            preds = preds.reshape(-1, preds.shape[-1])
            loss = criterion(preds, batch_y)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), args.clip)
            optimizer.step()

            batch_size = batch_X.size(1)
            total_batch_size += batch_size
            epoch_loss += loss.item() * batch_size
            aps = average_precision_score(batch_y.data.cpu().numpy().flatten(), preds.data.cpu().numpy().flatten())
            total_aps += batch_size * aps
        return epoch_loss / len(training_set), total_aps/float(total_batch_size)

    def evaluate(model, criterion):
        model.eval()
        epoch_loss = 0.0
        loader = test_loader
        total_batch_size = 0
        total_aps = 0.0
        with torch.no_grad():
            for i_batch, (batch_X, batch_y) in enumerate(loader):
                cur_batch_size = len(batch_X)
                # For distributed
                #with torch.cuda.device(0):
                #    batch_X = batch_X.float().cuda()
                #    batch_y = batch_y.float().cuda()
                batch_X, batch_y = batch_X.float().to(device=device), batch_y.float().to(device=device)
                batch_X = batch_X.transpose(0, 1).float()
                preds, _ = model(batch_X)
                preds = preds.transpose(0, 1)
                # reshape batch_y and preds to be of size (N, feature_dim) for loss calculation
                batch_y = batch_y.reshape(-1, batch_y.shape[-1])
                preds = preds.reshape(-1, preds.shape[-1])
                loss = criterion(preds, batch_y)
                batch_size = batch_X.size(1)
                total_batch_size += batch_size
                epoch_loss += loss.item() * batch_size
                #print(batch_y[0:100])
                #print(preds[0:100])
                aps = average_precision_score(batch_y.data.cpu().numpy().flatten(), preds.data.cpu().numpy().flatten())
                total_aps += batch_size * aps
        return epoch_loss / len(test_set), total_aps/float(total_batch_size)



    for epoch in range(args.num_epochs):
        start = time.time() 

        train_loss, acc_train = train(model, optimizer, criterion)
        print('Epoch {:2d} | Train Loss {:5.4f} | Accuracy {:5.4f}'.format(epoch, train_loss, acc_train))
        test_loss, acc_test = evaluate(model, criterion)
        scheduler.step(test_loss)
        print("-"*50)
        print('Epoch {:2d} | Test  Loss {:5.4f} | Accuracy {:5.4f}'.format(epoch, test_loss, acc_test))
        print("-"*50)

        end = time.time()
        print("time: %d" % (end - start))

device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")

print(sys.argv)
parser = argparse.ArgumentParser(description='Signal Data Analysis')
parser.add_argument('-f', default='', type=str)
parser.add_argument('--model', type=str, default='Transformer',
                    help='name of the model to use (Transformer, etc.)')
parser.add_argument('--dataset', type=str, default='data',
                    help='path for storing the dataset')
parser.add_argument('--time_step', type=int, default=20,
                    help='number of time step for each sequence')
parser.add_argument('--attn_dropout', type=float, default=0.0,
                    help='attention dropout')
parser.add_argument('--relu_dropout', type=float, default=0.1,
                    help='relu dropout')
parser.add_argument('--res_dropout', type=float, default=0.1,
                    help='residual block dropout')
parser.add_argument('--nlevels', type=int, default=6,
                    help='number of layers in the network (if applicable) (default: 6)')
parser.add_argument('--nhorizons', type=int, default=1)
parser.add_argument('--modal_lengths', nargs='+', type=int, default=[64, 64],
                    help='lengths of each modality (default: [64, 64])')
parser.add_argument('--output_dim', type=int, default=128,
                    help='dimension of output (default: 128)')
parser.add_argument('--num_epochs', type=int, default=200,
                    help='number of epochs (default: 200)')
parser.add_argument('--num_heads', type=int, default=8,
                    help='number of heads for the transformer network')
parser.add_argument('--seed', type=int, default=1111,
                    help='random seed')
parser.add_argument('--batch_size', type=int, default=64, metavar='N',
                    help='batch size (default: 64)')
parser.add_argument('--attn_mask', action='store_true',
                    help='use attention mask for Transformer (default: False)')
parser.add_argument('--crossmodal', action='store_false',
                    help='determine whether use the crossmodal fusion or not (default: True)')
parser.add_argument('--lr', type=float, default=1e-3,
                    help='initial learning rate (default: 1e-3)')
parser.add_argument('--clip', type=float, default=0.35,
                    help='gradient clip value (default: 0.35)')
parser.add_argument('--optim', type=str, default='Adam',
                    help='optimizer to use (default: Adam)')
parser.add_argument('--hidden_size', type=int, default=200,
                    help='hidden_size in transformer (default: 200)')
# For distributed
#parser.add_argument("--local_rank", type=int)
args = parser.parse_args()

torch.manual_seed(args.seed)
print(args)

# For distributed
#torch.cuda.set_device(args.local_rank)
use_cuda = True

# For distributed
#torch.distributed.init_process_group(backend='nccl', init_method='env://')

"""
Data Loading
"""

torch.set_default_tensor_type('torch.FloatTensor')
print("Start loading the data....")
    
training_set = SignalDataset_iq(args.dataset, args.time_step, train=True)
test_set = SignalDataset_iq(args.dataset, args.time_step, train=False)

print("Finish loading the data....")

train_loader = torch.utils.data.DataLoader(training_set, batch_size=args.batch_size, shuffle=True)
test_loader = torch.utils.data.DataLoader(test_set, batch_size=args.batch_size, shuffle=True)

train_transformer()

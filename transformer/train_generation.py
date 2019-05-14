import os,sys,inspect
currentdir = os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe())))
parentdir = os.path.dirname(currentdir)
sys.path.insert(0,parentdir) 

import torch
from torch import nn
from utils import SignalDataset_iq, SignalDataset_music, count_parameters
import argparse
from model import *
import torch.optim as optim
import numpy as np
import time
from torch.optim.lr_scheduler import ReduceLROnPlateau
from torch.utils.data import DataLoader
import os
import time

# Should change into only two modalities, instead of three
def train_transformer():
    if args.data == 'iq': 
        input_size = int(3200 / (args.src_time_step + args.trg_time_step))
    else: 
        input_size = 4096 
    input_dim = int(input_size / 2) 

    model = TransformerGenerationModel(ntokens=10000,        # TODO: wait for Paul's data
                             # time_step=args.time_step,
                             input_dims=[input_dim, input_dim],
                             # proj_dims=args.modal_lengths,
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
    # criterion = nn.CrossEntropyLoss()
    criterion= nn.MSELoss() 

    scheduler = ReduceLROnPlateau(optimizer, mode='min', patience=2, factor=0.5, verbose=True)

    settings = {'model': model,
                'optimizer': optimizer,
                'criterion': criterion,
                'scheduler': scheduler, 
                'input_size': input_size, 
                'src_time_step': args.src_time_step,
                'trg_time_step': args.trg_time_step}
    return train_model(settings)


def train_model(settings):
    model = settings['model']
    optimizer = settings['optimizer']
    criterion = settings['criterion']
    scheduler = settings['scheduler']
    input_size = settings['input_size']
    src_time_step = settings['src_time_step']
    trg_time_step = settings['trg_time_step']


    def train(model, optimizer, criterion):
        epoch_loss = 0
        
        model.train()

        start_time = time.time()
        for i_batch, (data_batched, _) in enumerate(train_loader):
            cur_batch_size = len(data_batched) 

            # src = data_batched[:, 0 : src_time_step * input_size] 
            # trg = data_batched[:, src_time_step * input_size : ]
            # src = src.reshape(cur_batch_size, src_time_step, input_size) 
            # trg = trg.reshape(cur_batch_size, trg_time_step, input_size)
            src = data_batched[:, 0 : src_time_step, :]
            trg = data_batched[:, src_time_step : , :]
            src = src.transpose(1, 0) # (ts, bs, input_size)
            trg = trg.transpose(1, 0) # (ts, bs, input_size)
            src = src.float().cuda()
            trg = trg.float().cuda() 

            # clear gradients
            model.zero_grad() 

            # batch_X = batch_X.cuda()
            # batch_X = batch_X.transpose(0, 1).float() # (seq_len, batch_size, feature_dim)

            outputs = model(x=src, y=trg) 

            trg = trg.transpose(1, 0).reshape(cur_batch_size, -1)
            outputs = outputs.transpose(1, 0).reshape(cur_batch_size, -1)


            loss = criterion(outputs, trg)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), args.clip)
            optimizer.step()

            # batch_size = batch_X.size(1)
            # proc_loss += loss.item() * batch_size
            # proc_size += batch_size
            # epoch_loss += loss.item() * batch_size
            epoch_loss += loss 
#             if i_batch % args.log_interval == 0 and i_batch > 0:
#                 avg_loss = proc_loss / proc_size
#                 elapsed_time = time.time() - start_time
#                 print('Epoch {:2d} | Batch {:3d}/{:3d} | Time/Batch(ms) {:5.2f} | Train Loss {:5.4f}'.format(epoch, i_batch, num_batches, elapsed_time * 1000 / args.log_interval, avg_loss))
#                 proc_loss, proc_size = 0, 0
#                 start_time = time.time()
            # total_pred += cur_batch_size 
            # total_correct += (torch.argmax(preds, dim=1)==batch_y).sum()

        avg_loss = epoch_loss / float(len(train_loader))


        end = time.time()
        print("time: %d" % (end - start))

        return avg_loss

        # return epoch_loss / len(training_set), float(total_correct)/float(total_pred)

    def evaluate(model, criterion):
        model.eval()
        epoch_loss = 0

#         results = []
#         truths = []
        with torch.no_grad():
            for i_batch, (data_batched, _) in enumerate(test_loader):
                cur_batch_size = len(data_batched) 

                # src = data_batched[:, 0 : src_time_step * input_size] 
                # trg = data_batched[:, src_time_step * input_size : ]
                # src = src.reshape(cur_batch_size, src_time_step, input_size) 
                # trg = trg.reshape(cur_batch_size, trg_time_step, input_size)
                src = data_batched[:, 0 : src_time_step, :]
                trg = data_batched[:, src_time_step : , :]
                src = src.transpose(1, 0) # (ts, bs, input_size)
                trg = trg.transpose(1, 0) # (ts, bs, input_size)
                src = src.float().cuda()
                trg = trg.float().cuda() 

                outputs = model(x=src, y=trg) 

                trg = trg.transpose(1, 0).reshape(cur_batch_size, -1)
                outputs = outputs.transpose(1, 0).reshape(cur_batch_size, -1)

                loss = criterion(outputs, trg)
                epoch_loss += loss 

        avg_loss = epoch_loss / float(len(test_loader))
        return avg_loss



    best_valid = 1e8
    for epoch in range(args.num_epochs):
        start = time.time() 

        train_loss = train(model, optimizer, criterion)
        print('Epoch {:2d} | Train Loss {:5.4f}'.format(epoch, train_loss))
        test_loss = evaluate(model, criterion)
        scheduler.step(test_loss)
        print("-"*50)
        print('Epoch {:2d} | Test  Loss {:5.4f}'.format(epoch, test_loss))
        print("-"*50)

        end = time.time()
        print("time: %d" % (end - start))


def weighted_accuracy(test_preds_emo, test_truth_emo):
    true_label = (test_truth_emo > 0)
    predicted_label = (test_preds_emo > 0)
    tp = float(np.sum((true_label==1) & (predicted_label==1)))
    tn = float(np.sum((true_label==0) & (predicted_label==0)))
    p = float(np.sum(true_label==1))
    n = float(np.sum(true_label==0))
    
    
    return (tp * (n/p) +tn) / (2*n)




parser = argparse.ArgumentParser(description='Signal Data Analysis')
parser.add_argument('-f', default='', type=str)
parser.add_argument('--model', type=str, default='Transformer',
                    help='name of the model to use (Transformer, etc.)')
parser.add_argument('--data', type=str, default='music')
parser.add_argument('--path', type=str, default='data',
                    help='path for storing the dataset')
# parser.add_argument('--time_step', type=int, default=20,
#                     help='number of time step for each sequence')
parser.add_argument('--src_time_step', type=int, default=30)
parser.add_argument('--trg_time_step', type=int, default=20)
parser.add_argument('--attn_dropout', type=float, default=0.0,
                    help='attention dropout')
parser.add_argument('--relu_dropout', type=float, default=0.1,
                    help='relu dropout')
parser.add_argument('--res_dropout', type=float, default=0.1,
                    help='residual block dropout')
parser.add_argument('--nlevels', type=int, default=6,
                    help='number of layers in the network (if applicable) (default: 6)')
parser.add_argument('--nhorizons', type=int, default=1)
# parser.add_argument('--modal_lengths', nargs='+', type=int, default=[160, 160],
#                     help='lengths of each modality (default: [160, 160])')
parser.add_argument('--output_dim', type=int, default=1000,
                    help='dimension of output (default: 1000)')
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
args = parser.parse_args()

torch.manual_seed(args.seed)
print(args)

# Assume cuda is used
use_cuda = True

"""
Data Loading
"""

torch.set_default_tensor_type('torch.FloatTensor')
# if torch.cuda.is_available():
#     if args.no_cuda:
#         print("WARNING: You have a CUDA device, so you should probably not run with --no_cuda")
#     else:
#         torch.cuda.manual_seed(args.seed)
#         torch.set_default_tensor_type('torch.cuda.FloatTensor')
#         use_cuda = True

total_time_step = args.src_time_step + args.trg_time_step
start = time.time()
print("Start loading the data....")
    
if args.data == 'iq': 
    training_set = SignalDataset_iq(args.path, time_step=total_time_step, train=True)
    test_set = SignalDataset_iq(args.path, time_step=total_time_step, train=False)
else: 
    assert(total_time_step == 128 or total_time_step == 256)
    training_set = SignalDataset_music(args.path, time_step=total_time_step, train=True)
    test_set = SignalDataset_music(args.path, time_step=total_time_step, train=False)

train_loader = torch.utils.data.DataLoader(training_set, batch_size=args.batch_size, shuffle=True)
test_loader = torch.utils.data.DataLoader(test_set, batch_size=args.batch_size, shuffle=True)

end = time.time() 
print("Loading data time: %d" % (end - start))

train_transformer()
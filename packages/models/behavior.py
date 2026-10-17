import torch
from torch import nn
from torch.nn.utils.rnn import pack_padded_sequence

class BehaviorModel(nn.Module):
    def __init__(self,state_size=45,width=32,context=True):
        super().__init__();self.context=context;self.width=width
        if context:self.sequence=nn.GRU(9,width,batch_first=True)
        self.head=nn.Linear(state_size+(width if context else 0),7)
    def forward(self,state,sequence,lengths,mask):
        if self.context:
            _,hidden=self.sequence(pack_padded_sequence(sequence,lengths.cpu(),batch_first=True,enforce_sorted=False))
            state=torch.cat([state,hidden[-1]],dim=1)
        return self.head(state).masked_fill(~mask,-1e9)

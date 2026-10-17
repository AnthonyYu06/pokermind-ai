"""Compact supervised equity network. Inputs are river-only set summaries."""
import torch
from torch import nn
from packages.features.equity import FEATURE_DIM


class EquityMLP(nn.Module):
    def __init__(self):
        super().__init__()
        self.net=nn.Sequential(nn.Linear(FEATURE_DIM,64),nn.ReLU(),nn.Linear(64,32),nn.ReLU(),nn.Linear(32,1),nn.Sigmoid())

    def forward(self,x):return self.net(x).squeeze(-1)

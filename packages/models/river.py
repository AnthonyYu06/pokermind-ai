"""River-only residual network with decision-visible structural features.

Hand rank is computed from the acting player's seven known cards, never from an
opponent, a rollout, or the equity label. The raw-count ablation omits that rank.
"""
import torch
from torch import nn
from packages.features.equity import encode
from packages.pokermind_core.cards import rank, reference_five

VERSION = 'river-structure-v1'
DIM = 75


def encode_structure(hero, board):
    raw = encode(hero, board)
    value = rank(hero + board)
    # Treys category boundaries, strongest first. These are evaluator semantics,
    # not quantities learned from the reference labels.
    category = sum(value > boundary for boundary in (10, 166, 322, 1599, 1609, 2467, 3325, 6185))
    public_category = reference_five(board)[0]
    return raw + [value / 7462, (7462 - value) / 7462] + [float(category == i) for i in range(9)] + [float(public_category == i) for i in range(9)]


class RiverNet(nn.Module):
    def __init__(self, structural=True, width=96):
        super().__init__()
        self.structural, self.width = structural, width
        self.input = nn.Sequential(nn.Linear(DIM if structural else 55, width), nn.ReLU())
        self.blocks = nn.ModuleList([nn.Sequential(nn.Linear(width, width), nn.ReLU(), nn.Linear(width, width)) for _ in range(2)])
        self.output = nn.Linear(width, 1)

    def forward(self, x):
        x = self.input(x if self.structural else x[..., :55])
        for block in self.blocks:
            x = torch.relu(x + block(x))
        return self.output(x).squeeze(-1).sigmoid()

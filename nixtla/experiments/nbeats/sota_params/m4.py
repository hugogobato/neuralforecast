# AUTOGENERATED! DO NOT EDIT! File to edit: nbs/experiments_nbeats_sota_params__m4.ipynb (unless otherwise specified).

__all__ = ['common_grid', 'common_ensemble_grid', 'Yearly', 'Quarterly', 'Monthly', 'Weekly', 'Daily', 'Hourly',
           'M4Params']

# Cell
from dataclasses import dataclass

from ....data.datasets.utils import Info
from ....data.datasets.m4 import M4Info

# Cell
common_grid = {'shared_weights': [True],
               'stack_types': [['trend', 'seasonality']],
               'n_blocks': [[3, 3]],
               'n_layers': [[2, 2]],
               'n_hidden': [[256, 2048]],
               'n_harmonics': [1],
               'n_polynomials': [2],
               'learning_rate': [0.001],
               'lr_decay': [0.5],
               'n_lr_decay_steps': [3],
               'batch_size': [1024]}
common_ensemble_grid = {'input_size_multiplier': [2, 3, 4, 5, 6, 7],
                        'random_seed': [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
                        'loss': ['SMAPE']}

# Cell
@dataclass
class Yearly:
    grid = {**common_grid,
            **{'output_size': [M4Info['Yearly'].horizon],
               'offset': [M4Info['Yearly'].horizon],
               'window_sampling_limit_multiplier': [1.5],
               'n_iterations': [15_000],
               'frequency': ['Y'],
               'seasonality': [1]}}

    ensemble_grid = common_ensemble_grid

# Cell
@dataclass
class Quarterly:
    grid = {**common_grid,
            **{'output_size': [M4Info['Quarterly'].horizon],
               'offset': [M4Info['Quarterly'].horizon],
               'window_sampling_limit_multiplier': [1.5],
               'n_iterations': [15_000],
               'frequency': ['Q'],
               'seasonality': [4]}}

    ensemble_grid = common_ensemble_grid

# Cell
@dataclass
class Monthly:
    grid = {**common_grid,
            **{'output_size': [M4Info['Monthly'].horizon],
               'offset': [M4Info['Monthly'].horizon],
               'window_sampling_limit_multiplier': [1.5],
               'n_iterations': [15_000],
               'frequency': ['MS'],
               'seasonality': [12]}}

    ensemble_grid = common_ensemble_grid

# Cell
@dataclass
class Weekly:
    grid = {**common_grid,
            **{'output_size': [M4Info['Weekly'].horizon],
               'offset': [M4Info['Weekly'].horizon],
               'window_sampling_limit_multiplier': [10],
               'n_iterations': [5_000],
               'frequency': ['W'],
               'seasonality': [4]}}

    ensemble_grid = common_ensemble_grid

# Cell
@dataclass
class Daily:
    grid = {**common_grid,
            **{'output_size': [M4Info['Daily'].horizon],
               'offset': [M4Info['Daily'].horizon],
               'window_sampling_limit_multiplier': [10],
               'n_iterations': [5_000],
               'frequency': ['D'],
               'seasonality': [7]}}

    ensemble_grid = common_ensemble_grid

# Cell
@dataclass
class Hourly:
    grid = {**common_grid,
            **{'output_size': [M4Info['Hourly'].horizon],
               'offset': [M4Info['Hourly'].horizon],
               'window_sampling_limit_multiplier': [10],
               'n_iterations': [5_000],
               'frequency': ['H'],
               'seasonality': [24]}}

    ensemble_grid = common_ensemble_grid

# Cell
M4Params = Info(groups=('Yearly', 'Quarterly', 'Monthly', 'Weekly', 'Daily', 'Hourly'),
                class_groups=(Yearly, Quarterly, Monthly, Weekly, Daily, Hourly))
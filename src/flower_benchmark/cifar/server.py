# Copyright 2020 Adap GmbH. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================
"""Baseline server."""


import argparse
import math
from logging import ERROR, INFO
from typing import Callable, Dict, Optional, Tuple

import numpy as np
import torch

import flower as fl
from flower.logger import configure, log
from flower_benchmark.dataset import tf_cifar_partitioned

from . import DEFAULT_SERVER_ADDRESS, cifar
from .settings import SETTINGS, get_setting

# pylint: disable=no-member
DEVICE = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
# pylint: enable=no-member


def parse_args() -> argparse.Namespace:
    """Parse and return commandline arguments."""
    parser = argparse.ArgumentParser(description="Flower")
    parser.add_argument(
        "--log_host", type=str, help="HTTP log handler host (no default)",
    )
    parser.add_argument(
        "--setting", type=str, choices=SETTINGS.keys(), help="Setting to run.",
    )

    return parser.parse_args()


def main() -> None:
    """Start server and train a number of rounds."""
    args = parse_args()

    # Configure logger
    configure(identifier="server", host=args.log_host)

    server_setting = get_setting(args.setting).server
    log(INFO, "server_setting: %s", server_setting)

    # Load evaluation data
    (_, _), (x_test, y_test) = tf_cifar_partitioned.load_data(
        iid_fraction=1.0, num_partitions=1
    )
    if server_setting.dry_run:
        x_test = x_test[0:50]
        y_test = y_test[0:50]

    # Load model (for centralized evaluation)
    model = cifar.load_model()

    # Create client_manager
    client_manager = fl.SimpleClientManager()

    # Strategy
    eval_fn = get_eval_fn(model=model, num_classes=10, xy_test=(x_test, y_test))
    on_fit_config_fn = get_on_fit_config_fn(
        lr_initial=server_setting.lr_initial,
        timeout=server_setting.training_round_timeout,
        partial_updates=server_setting.partial_updates,
    )

    if server_setting.strategy == "fedavg":
        strategy = fl.strategy.FedAvg(
            fraction_fit=server_setting.sample_fraction,
            min_fit_clients=server_setting.min_sample_size,
            min_available_clients=server_setting.min_num_clients,
            eval_fn=eval_fn,
            on_fit_config_fn=on_fit_config_fn,
        )

    if server_setting.strategy == "fast-and-slow":
        if server_setting.training_round_timeout is None:
            raise ValueError(
                "No `training_round_timeout` set for `fast-and-slow` strategy"
            )
        t_fast = (
            math.ceil(0.5 * server_setting.training_round_timeout)
            if server_setting.training_round_timeout_short is None
            else server_setting.training_round_timeout_short
        )
        strategy = fl.strategy.FastAndSlow(
            fraction_fit=server_setting.sample_fraction,
            min_fit_clients=server_setting.min_sample_size,
            min_available_clients=server_setting.min_num_clients,
            eval_fn=eval_fn,
            on_fit_config_fn=on_fit_config_fn,
            importance_sampling=server_setting.importance_sampling,
            dynamic_timeout=server_setting.dynamic_timeout,
            dynamic_timeout_percentile=0.8,
            alternating_timeout=server_setting.alternating_timeout,
            r_fast=1,
            r_slow=1,
            t_fast=t_fast,
            t_slow=server_setting.training_round_timeout,
        )

    if server_setting.strategy == "fedfs-v0":
        if server_setting.training_round_timeout is None:
            raise ValueError("No `training_round_timeout` set for `fedfs-v0` strategy")
        t_fast = (
            math.ceil(0.5 * server_setting.training_round_timeout)
            if server_setting.training_round_timeout_short is None
            else server_setting.training_round_timeout_short
        )
        strategy = fl.strategy.FedFSv0(
            fraction_fit=server_setting.sample_fraction,
            min_fit_clients=server_setting.min_sample_size,
            min_available_clients=server_setting.min_num_clients,
            eval_fn=eval_fn,
            on_fit_config_fn=on_fit_config_fn,
            r_fast=1,
            r_slow=1,
            t_fast=t_fast,
            t_slow=server_setting.training_round_timeout,
        )

    if server_setting.strategy == "fedfs-v1":
        if server_setting.training_round_timeout is None:
            raise ValueError("No `training_round_timeout` set for `fedfs-v1` strategy")
        strategy = fl.strategy.FedFSv1(
            fraction_fit=server_setting.sample_fraction,
            min_fit_clients=server_setting.min_sample_size,
            min_available_clients=server_setting.min_num_clients,
            eval_fn=eval_fn,
            on_fit_config_fn=on_fit_config_fn,
            dynamic_timeout_percentile=0.8,
            r_fast=1,
            r_slow=1,
            t_max=server_setting.training_round_timeout,
            use_past_contributions=True,
        )

    if server_setting.strategy == "qffedavg":
        strategy = fl.strategy.QffedAvg(
            q_param=0.2,
            qffl_learning_rate=0.1,
            fraction_fit=server_setting.sample_fraction,
            min_fit_clients=server_setting.min_sample_size,
            min_available_clients=server_setting.min_num_clients,
            eval_fn=eval_fn,
            on_fit_config_fn=on_fit_config_fn,
        )

    # Run server
    log(INFO, "Instantiating server, strategy: %s", str(strategy))
    server = fl.Server(client_manager=client_manager, strategy=strategy)
    fl.app.start_server(
        DEFAULT_SERVER_ADDRESS, server, config={"num_rounds": server_setting.rounds},
    )


def get_on_fit_config_fn(
    lr_initial: float, timeout: Optional[int], partial_updates: bool
) -> Callable[[int], Dict[str, str]]:
    """Return a function which returns training configurations."""

    def fit_config(rnd: int) -> Dict[str, str]:
        """Return a configuration with static batch size and (local) epochs."""
        config = {
            "epoch_global": str(rnd),
            "epochs": str(1),
            "batch_size": str(10),
            "lr_initial": str(lr_initial),
            "lr_decay": str(0.99),
            "partial_updates": "1" if partial_updates else "0",
        }
        if timeout is not None:
            config["timeout"] = str(timeout)

        return config

    return fit_config


# pylint: disable=unused-argument
def get_eval_fn(
    model: cifar.Net, num_classes: int, xy_test: Tuple[np.ndarray, np.ndarray]
) -> Callable[[fl.Weights], Optional[Tuple[float, float]]]:
    """Return an evaluation function for centralized evaluation."""

    testset = cifar.ds_from_nda(xy_test[0], xy_test[1])

    def evaluate(weights: fl.Weights) -> Optional[Tuple[float, float]]:
        """Use entire test set for evaluation."""
        model.set_weights(weights)
        testloader = torch.utils.data.DataLoader(testset, batch_size=32, shuffle=False)
        loss, accuracy = cifar.test(model, testloader, device=DEVICE)
        return loss, accuracy

    return evaluate


if __name__ == "__main__":
    # pylint: disable=broad-except
    try:
        main()
    except Exception as err:
        log(ERROR, "Fatal error in main")
        log(ERROR, err, exc_info=True, stack_info=True)

        # Raise the error again so the exit code is correct
        raise err

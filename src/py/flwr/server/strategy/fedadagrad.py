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
"""Adaptive Federated Optimization using Adagrad (FedAdagrad) [Reddi et al.,
2020] strategy.

Paper: https://arxiv.org/abs/2003.00295
"""


from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

from flwr.common import FitRes, Weights
from flwr.server.client_proxy import ClientProxy

from .fedopt import FedOpt


class FedAdagrad(FedOpt):
    """Configurable FedAdagrad strategy implementation."""

    # pylint: disable-msg=too-many-arguments,too-many-instance-attributes
    def __init__(
        self,
        fraction_fit: float = 0.1,
        fraction_eval: float = 0.1,
        min_fit_clients: int = 2,
        min_eval_clients: int = 2,
        min_available_clients: int = 2,
        eval_fn: Optional[Callable[[Weights], Optional[Tuple[float, float]]]] = None,
        on_fit_config_fn: Optional[Callable[[int], Dict[str, str]]] = None,
        on_evaluate_config_fn: Optional[Callable[[int], Dict[str, str]]] = None,
        accept_failures: bool = True,
        eta: float = 1e-1,
        eta_l: float = 1e-1,
        tau: float = 1e-9,
    ) -> None:
        super().__init__(
            fraction_fit=fraction_fit,
            fraction_eval=fraction_eval,
            min_fit_clients=min_fit_clients,
            min_eval_clients=min_eval_clients,
            min_available_clients=min_available_clients,
            eval_fn=eval_fn,
            on_fit_config_fn=on_fit_config_fn,
            on_evaluate_config_fn=on_evaluate_config_fn,
            accept_failures=accept_failures,
            eta=eta,
            eta_l=eta_l,
            tau=tau,
        )
        self.v_t: Optional[np.ndarray] = None

    def __repr__(self) -> str:
        rep = f"FedAdagrad(accept_failures={self.accept_failures})"
        return rep

    def aggregate_fit(
        self,
        rnd: int,
        results: List[Tuple[ClientProxy, FitRes]],
        failures: List[BaseException],
        previous_weights: Weights = [],
    ) -> Optional[Weights]:
        """Aggregate fit results using weighted average."""
        fedavg_aggregate = super().aggregate_fit(
            rnd=rnd, results=results, failures=failures
        )
        if fedavg_aggregate is None:
            return None
        aggregated_updates = fedavg_aggregate[0] - previous_weights[0]

        # Adagrad
        delta_t = aggregated_updates
        if not self.v_t:
            self.v_t = np.zeros_like(delta_t)
        self.v_t = self.v_t + np.multiply(delta_t, delta_t)

        weights = [
            previous_weights[0] + self.eta * delta_t / (np.sqrt(self.v_t) + self.tau)
        ]

        return weights

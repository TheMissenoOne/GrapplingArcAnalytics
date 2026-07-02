"""Graph Neural Network predictor for match outcomes — GCN baseline.
# ruff: noqa: N803, N806, E501

Builds a fighter×fight bipartite graph where:
  - Fighter nodes carry style-vector features (the 8-bucket type shares).
  - Fight nodes carry year / stage / weight-class features.
  - Edges link a fighter to a fight they participated in, labelled WIN/LOSS.

A 2-layer Graph Convolutional Network produces fighter embeddings that are then
fed to a bilinear classifier for match-outcome prediction.  Full gradient
backpropagation through all layers (pure NumPy).

Reference
---------
Drexler (2024). "Sports Analytics with Graph Neural Networks and Graph
Convolutional Networks." *Preprints*.
"""

from __future__ import annotations

import numpy as np


class GCNPredictor:
    """2-layer GCN for match outcome prediction on a fighter-fight bipartite graph.

    Pure NumPy implementation (no PyTorch Geometric dependency).  Uses a simple
    graph convolutional layer: ``H' = σ(A_hat @ H @ W)`` where ``A_hat`` is the
    symmetrically normalised adjacency with self-loops.

    Parameters
    ----------
    hidden_dim : int
        Hidden dimension (default 32).
    output_dim : int
        Output embedding dimension per node (default 16).
    lr : float
        Learning rate for SGD (default 0.01).
    reg : float
        L2 regularisation strength (default 1e-4).
    """

    def __init__(
        self,
        hidden_dim: int = 32,
        output_dim: int = 16,
        lr: float = 0.01,
        reg: float = 1e-4,
    ):
        self.hidden_dim = hidden_dim
        self.output_dim = output_dim
        self.lr = lr
        self.reg = reg
        self.params: dict[str, np.ndarray] = {}
        self._cache: dict[str, np.ndarray] = {}

    def _init_params(self, feat_dim: int) -> None:
        rng = np.random.RandomState(42)
        scale0 = np.sqrt(2.0 / feat_dim)
        scale1 = np.sqrt(2.0 / self.hidden_dim)
        self.params = {
            "W0": rng.randn(feat_dim, self.hidden_dim).astype(np.float64) * scale0,
            "b0": np.zeros(self.hidden_dim, dtype=np.float64),
            "W1": rng.randn(self.hidden_dim, self.output_dim).astype(np.float64) * scale1,
            "b1": np.zeros(self.output_dim, dtype=np.float64),
            "U": rng.randn(self.output_dim).astype(np.float64) * 0.01,
        }

    @staticmethod
    def _normalise_adj(adj: np.ndarray) -> np.ndarray:
        """Symmetrically normalise adjacency with self-loops: D^{-1/2} (A+I) D^{-1/2}."""
        n = adj.shape[0]
        a_hat = adj + np.eye(n, dtype=np.float64)
        d = np.array(a_hat.sum(axis=1)).ravel()
        d_inv_sqrt = np.where(d > 0, 1.0 / np.sqrt(d), 0.0)
        return a_hat * d_inv_sqrt[np.newaxis, :] * d_inv_sqrt[:, np.newaxis]

    def _forward(self, H: np.ndarray, a_hat: np.ndarray) -> np.ndarray:
        """Propagate through GCN layers. Caches intermediates for backward."""
        H0_raw = a_hat @ H @ self.params["W0"] + self.params["b0"]
        H0 = np.maximum(H0_raw, 0)
        H1 = a_hat @ H0 @ self.params["W1"] + self.params["b1"]
        self._cache = {"H": H, "H0_raw": H0_raw, "H0": H0, "a_hat": a_hat}
        return H1

    def fit(
        self,
        fighter_feats: np.ndarray,
        adj: np.ndarray,
        match_fighter_pairs: list[tuple[int, int, float]],
        n_fight_nodes: int,
        fight_feats: np.ndarray | None = None,
        epochs: int = 100,
        verbose: bool = False,
    ) -> list[float]:
        """Fit the GCN on a fighter-fight bipartite graph.

        Parameters
        ----------
        fighter_feats : ndarray, shape (n_fighters, n_fighter_feat)
            Style-vector features for each fighter node.
        adj : ndarray, shape (N, N)  where N = n_fighters + n_fight_nodes
            Full adjacency matrix (fighters first, then fights).  ``adj[i,j] = 1``
            if fighter i participated in fight j.
        match_fighter_pairs : list of (i, j, y)
            Training edges: index of fighter *in the fighter block*, index of
            fight node *in the fight block*, outcome y ∈ {0, 1}.
        n_fight_nodes : int
            Number of fight nodes in the graph.
        fight_feats : ndarray or None
            Optional features for fight nodes (shape ``(n_fight_nodes, n_fight_feat)``).
            If None, uses one-hot identity features.
        epochs : int
            Number of training epochs.
        verbose : bool
            Print loss every 10 epochs.

        Returns
        -------
        list[float]
            Loss per epoch.
        """
        n_f = fighter_feats.shape[0]
        n_total = n_f + n_fight_nodes

        if fight_feats is None:
            fight_feats = np.eye(n_fight_nodes, dtype=np.float64)
        feat_dim = fighter_feats.shape[1] + fight_feats.shape[1]
        self._init_params(feat_dim)

        H_full = np.zeros((n_total, feat_dim), dtype=np.float64)
        H_full[:n_f, :fighter_feats.shape[1]] = fighter_feats
        H_full[n_f:, fighter_feats.shape[1]:] = fight_feats

        a_hat = self._normalise_adj(adj)
        # Precompute A_hat^T once.
        a_hat_t = a_hat.T
        losses: list[float] = []

        for epoch in range(epochs):
            embeddings = self._forward(H_full, a_hat)
            fighter_emb = embeddings[:n_f]

            # Accumulate gradients per pair.
            grad_H1 = np.zeros_like(embeddings)
            grad_U = np.zeros_like(self.params["U"])
            loss = 0.0

            for fi, fj, y in match_fighter_pairs:
                diff = fighter_emb[fi] - fighter_emb[fj]
                logit = diff @ self.params["U"]
                p = 1.0 / (1.0 + np.exp(-np.clip(logit, -20, 20)))
                loss += -y * np.log(p + 1e-15) - (1 - y) * np.log(1 - p + 1e-15)

                grad = float(p - y)
                # dL/ddiff = grad * U
                grad_diff = grad * self.params["U"]
                grad_H1[fi] += grad_diff
                grad_H1[fj] -= grad_diff

                # Gradient for U.
                grad_U += grad * diff

            # L2 regularisation.
            reg_loss = 0.5 * self.reg * (
                np.sum(self.params["W0"] ** 2)
                + np.sum(self.params["W1"] ** 2)
                + np.sum(self.params["U"] ** 2)
            )
            loss += reg_loss
            losses.append(float(loss))

            # Backprop through H1 = A_hat @ H0 @ W1 + b1.
            H0 = self._cache["H0"]
            H0_raw = self._cache["H0_raw"]
            H_input = self._cache["H"]

            # dL/dW1 = H0^T @ (A_hat^T @ dL/dH1)
            grad_W1 = H0.T @ (a_hat_t @ grad_H1)
            # dL/db1 = sum over rows of grad_H1
            grad_b1 = grad_H1.sum(axis=0)
            # dL/dH0 = A_hat^T @ dL/dH1 @ W1^T
            grad_H0 = a_hat_t @ grad_H1 @ self.params["W1"].T

            # ReLU backward.
            grad_H0_raw = grad_H0 * (H0_raw > 0)

            # dL/dW0 = H_input^T @ (A_hat^T @ dL/dH0_raw)
            grad_W0 = H_input.T @ (a_hat_t @ grad_H0_raw)
            # dL/db0 = sum over rows of grad_H0_raw
            grad_b0 = grad_H0_raw.sum(axis=0)

            # SGD update.
            self.params["U"] -= self.lr * grad_U
            self.params["W0"] -= self.lr * (grad_W0 + self.reg * self.params["W0"])
            self.params["b0"] -= self.lr * grad_b0
            self.params["W1"] -= self.lr * (grad_W1 + self.reg * self.params["W1"])
            self.params["b1"] -= self.lr * grad_b1

            if verbose and epoch % 10 == 0:
                print(f"  epoch {epoch:3d}  loss {loss:.4f}")

        return losses

    def predict(self, fighter_emb_a: np.ndarray, fighter_emb_b: np.ndarray) -> float:
        """Predict probability that fighter A beats fighter B."""
        diff = fighter_emb_a - fighter_emb_b
        logit = diff @ self.params["U"]
        return float(1.0 / (1.0 + np.exp(-np.clip(logit, -20, 20))))

    def embed(self, fighter_feats: np.ndarray, adj: np.ndarray,
              n_fight_nodes: int, fight_feats: np.ndarray | None = None) -> np.ndarray:
        """Project fighter nodes to embedding space."""
        n_f = fighter_feats.shape[0]
        n_total = n_f + n_fight_nodes
        if fight_feats is None:
            fight_feats = np.eye(n_fight_nodes, dtype=np.float64)
        H_full = np.zeros((n_total, fighter_feats.shape[1] + fight_feats.shape[1]), dtype=np.float64)
        H_full[:n_f, :fighter_feats.shape[1]] = fighter_feats
        H_full[n_f:, fighter_feats.shape[1]:] = fight_feats
        a_hat = self._normalise_adj(adj)
        return self._forward(H_full, a_hat)[:n_f]

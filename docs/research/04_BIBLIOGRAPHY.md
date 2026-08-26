# Bibliography — analytics literature cross-reference

Every paper referenced by the literature review (`01_LITERATURE_REVIEW.md`), the gap
analysis (`02_GAPS_AND_OPPORTUNITIES.md`) and the PoC plans (`03_POC_PLANS.md`), with
links. Identifiers verified 2026-08-23 against the publishers' pages. Entries marked
**[in-code]** are already cited somewhere in this repo's source; the rest are new to
this review.

## A. Network science applied to sport

- Radicchi, F. (2011). **Who Is the Best Player Ever? A Complex Network Analysis of the
  History of Professional Tennis.** PLOS ONE 6(2):e17249.
  https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0017249
  — PageRank "prestige" on a player-vs-player match network out-predicted the official
  ATP ranking on match outcomes. The direct template for an athlete-level PageRank over
  the `matches` table.
- Duch, J., Waitzman, J.S., Amaral, L.A.N. (2010). **Quantifying the Performance of
  Individual Players in a Team Activity.** PLOS ONE 5(6):e10937.
  https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0010937
  — flow centrality in passing networks.
- Peña, J.L., Touchette, H. (2012). **A network theory analysis of football strategies.**
  arXiv:1206.6904. https://arxiv.org/abs/1206.6904 **[in-code**, cited in
  `docs/graph_analysis_approaches.md` via doi:10.1007/978-3-319-63907-9_16**]**
- Buldú, J.M., Busquets, J., Martínez, J.H., et al. (2018). **Using Network Science to
  Analyse Football Passing Networks: Dynamics, Space, Time, and the Multilayer Nature of
  the Game.** Frontiers in Psychology 9:1900.
  https://www.frontiersin.org/articles/10.3389/fpsyg.2018.01900/full
- **Motif analysis and passing behavior in football passing networks** (2024).
  arXiv:2408.07927. https://arxiv.org/pdf/2408.07927 — network motifs as style
  fingerprints; the motif alternative to our type-share style vectors.
- Zhang, P., Wang, T., Yan, J. (2022). **PageRank centrality and algorithms for
  weighted, directed networks.** Physica A 586:126438. doi:10.1016/j.physa.2021.126438,
  arXiv:2104.02764. https://arxiv.org/abs/2104.02764 **[in-code**,
  `analysis/network_metrics.py`**]**
- Judo attack-combination networks (2025). **The Dynamics of the "Gentle Way": Exploring
  Judo Attack Combinations as Networks in R.**
  https://geekcologist.wordpress.com/2025/05/27/the-dynamics-of-the-gentle-way-exploring-judo-attack-combinations-as-networks-in-r/
  — the closest published analogue of our technique-transition graphs (technique = node,
  combination = edge; initiators, finishers, influence). Blog-tier, but grappling-specific.

## B. Community detection

- Fortunato, S., Barthélemy, M. (2007). **Resolution limit in community detection.**
  PNAS 104(1):36–41. doi:10.1073/pnas.0605965104.
  https://www.pnas.org/doi/10.1073/pnas.0605965104
  — modularity maximisation cannot resolve communities below a scale set by total edge
  count; small well-defined groups get merged. Directly governs what Louvain at
  resolution 1.0 can see on 15–70-node user graphs and 200-node corpus graphs.
- Traag, V.A., Waltman, L., van Eck, N.J. (2019). **From Louvain to Leiden: guaranteeing
  well-connected communities.** Scientific Reports 9:5233. doi:10.1038/s41598-019-41695-z,
  arXiv:1810.08473. https://www.nature.com/articles/s41598-019-41695-z **[in-code**,
  `analysis/constellations/detect.py` and the App port**]** — up to 25% of Louvain
  communities badly connected, up to 16% disconnected; Leiden's refinement phase
  prevents (rather than post-hoc repairs) exactly the failure our ADR-07 gate counts.
- Rosvall, M., Bergstrom, C.T. (2008). **Maps of random walks on complex networks reveal
  community structure.** PNAS 105(4):1118–1123. doi:10.1073/pnas.0706851105.
  https://www.pnas.org/doi/10.1073/pnas.0706851105 (Infomap) — flow-based objective; a
  better conceptual fit than modularity for *directed transition* graphs, where a
  community should mean "a region walks stay inside".
- Good, B.H., de Montjoye, Y.-A., Clauset, A. (2010). **Performance of modularity
  maximization in practical contexts.** Physical Review E 81:046106.
  doi:10.1103/PhysRevE.81.046106, arXiv:0910.0165. https://arxiv.org/abs/0910.0165
  — the modularity landscape is glassy: exponentially many near-optimal partitions.
  One deterministic run (the App's sorted-order Louvain) is one sample from that set.
- Lee, C., Wilkinson, D.J. (2019). **A review of stochastic block models and extensions
  for graph clustering.** Applied Network Science 4:122. arXiv:1903.00114.
  https://arxiv.org/abs/1903.00114 — model-based inference alternative; degree-corrected
  SBM with model selection sidesteps the resolution limit.

## C. Rating systems

- Elo, A.E. (1978). **The Rating of Chessplayers, Past and Present.** Arco.
- Glickman, M.E. (2001). **Dynamic paired comparison models with stochastic variances.**
  Journal of Applied Statistics 28(6):673–689. doi:10.1080/02664760120059219.
- Glickman, M.E. **Example of the Glicko-2 system.**
  http://www.glicko.net/glicko/glicko2.pdf **[in-code** — the worked example both repos'
  Glicko-2 implementations are gate-verified against**]**
- Coulom, R. (2008). **Whole-History Rating: A Bayesian Rating System for Players of
  Time-Varying Strength.** Computers and Games 2008, LNCS 5131, pp. 113–124.
  https://www.remi-coulom.fr/WHR/WHR.pdf — MAP over each player's whole rating
  trajectory; beat Elo, Glicko, TrueSkill and decayed-history on prediction. The natural
  next engine for a *static-corpus, batch-recomputed* rating like ours.
- Herbrich, R., Minka, T., Graepel, T. (2007). **TrueSkill: A Bayesian Skill Rating
  System.** NIPS 19. https://papers.nips.cc/paper/3079-trueskilltm-a-bayesian-skill-rating-system
- Aldous, D. (2017). **Elo Ratings and the Sports Model: A Neglected Topic in Applied
  Probability?** Statistical Science 32(4):616–629. doi:10.1214/17-STS628.
  https://www.stat.berkeley.edu/~aldous/Papers/me-Elo-SS.pdf **[in-code**,
  `analysis/athlete_elo.py` — previously mis-dated "Aldous 2020"**]**
- Szczecinski, L., Djebbi, A. (2020). **Understanding draws in Elo rating algorithm.**
  Journal of Quantitative Analysis in Sports 16(3):211–220. doi:10.1515/jqas-2019-0102.
  — the principled (Elo-Davidson / Rao-Kupper) way to put draws in Elo; what
  `elo_calibration.draw_probability` should become if it ever meets a draw-bearing corpus.
- **Stochastic Extensions of the Elo Rating System** (2024). Applied Sciences
  14(17):8023. doi:10.3390/app14178023. https://www.mdpi.com/2076-3417/14/17/8023
  **[in-code**, `analysis/elo_calibration.py` — previously cited namelessly as
  "MDPI 2024"**]**
- **Empirical parameterization of the Elo Rating System** (2025). arXiv:2512.18013.
  https://arxiv.org/html/2512.18013v1 — fitting K and scale from data instead of
  convention; the method our `calibrate_k_factor` should use (predictive loss, not
  target-σ matching).
- Dehpanah, A., et al. (2021). **The Evaluation of Rating Systems in Team-based Battle
  Royale Games.** arXiv:2105.14069. https://arxiv.org/pdf/2105.14069 — head-to-head
  accuracy comparison framework (Glicko-2 63.1% vs Elo 62.8% on ~10k matches); a
  ready-made evaluation design.
- FightMatrix (2019). **Tuning Glicko: What I learned & confirmed.**
  https://www.fightmatrix.com/2019/09/18/tuning-glicko-what-i-learned-confirmed/
  — practitioner evidence that on sparse MMA schedules RD does little and volatility
  is nearly worthless; the priors to test when tuning τ on our sparse corpus.
- **An Adaptive Glicko-2 Rating Framework for Probabilistic Football Forecasting and
  Season Simulation** (2026). arXiv:2607.01722. https://arxiv.org/pdf/2607.01722

## D. Action / possession valuation

- Rudd, S. (2011). **A framework for tactical analysis and individual offensive
  production assessment in soccer using Markov chains.** NESSIS 2011.
  http://www.sloansportsconference.com/wp-content/uploads/2011/08/A-Framework-for-Tactical-Analysis-and-Individual-Offensive-Production-Assessment-in-Soccer-Using-Markov-Chains.pdf
- Singh, K. (2019). **Introducing Expected Threat (xT).**
  https://karun.in/blog/expected-threat.html **[in-code**, `docs/path_to_victory.md`**]**
- Decroos, T., Bransen, L., Van Haaren, J., Davis, J. (2019). **Actions Speak Louder
  than Goals: Valuing Player Actions in Soccer.** KDD '19. doi:10.1145/3292500.3330758,
  arXiv:1802.07127. https://arxiv.org/abs/1802.07127 (VAEP + SPADL) **[in-code]**
- Cervone, D., D'Amour, A., Bornn, L., Goldsberry, K. (2016). **A multiresolution
  stochastic process model for predicting basketball possession outcomes.** JASA
  111(514):585–599. doi:10.1080/01621459.2016.1141685 (EPV) **[in-code]**
- Routley, K., Schulte, O. (2015). **A Markov Game Model for Valuing Player Actions in
  Ice Hockey.** UAI 2015. https://www.auai.org/uai2015/proceedings/papers/70.pdf
  **[in-code]**
- Ng, A., Harada, D., Russell, S. (1999). **Policy invariance under reward
  transformations.** ICML 1999. **[in-code** — PtV's shaping term**]**
- Lamas, L., et al. (2024). **No-gi Brazilian jiu-jitsu: a Markovian analysis of
  elite-level combat dynamics.** International Journal of Sports Science & Coaching.
  doi:10.1177/17479541231210979 **[in-code** — the repo's reward/risk base; the ONE
  peer-reviewed BJJ Markov paper, with published transition probabilities
  (back-take→submission 0.45) our corpus can be benchmarked against**]**
- **A Semi-Markov framework for modeling football** (2026). Nature Scientific Reports.
  https://www.nature.com/articles/s41598-026-52938-1 — the memoryless assumption is
  systematically violated in match event streams; duration-aware semi-Markov corrections.
- **Model quality in football: Quantifying the quality of an Expected Threat model**
  (2026). arXiv:2604.21087. https://arxiv.org/pdf/2604.21087 — how to evaluate an
  xT-class model; the evaluation PtV never got.

## E. Sequential pattern mining

- Bunker, R., Fujii, K., Hanada, H., Takeuchi, I. (2021). **Supervised sequential
  pattern mining of event sequences in sport to identify important patterns of play: an
  application to rugby union.** PLOS ONE 16(9):e0256329. doi:10.1371/journal.pone.0256329,
  arXiv:2010.15377. https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0256329
  — PrefixSpan/CM-SPAM plus outcome-supervised importance; the same family (CM-SPAM) has
  been applied to judo technical-tactical analysis.
- Pei, J., Han, J., et al. (2004). **Mining Sequential Patterns by Pattern-Growth: The
  PrefixSpan Approach.** IEEE TKDE 16(11):1424–1440. doi:10.1109/TKDE.2004.77.

## F. Graph similarity and embeddings

- Grover, A., Leskovec, J. (2016). **node2vec: Scalable Feature Learning for Networks.**
  KDD '16. doi:10.1145/2939672.2939754, arXiv:1607.00653. https://arxiv.org/abs/1607.00653
  **[in-code**, `analysis/graph_embed.py`**]**
- Zhang, Y.-J., Yang, K.-C., Radicchi, F. (2021). **Systematic comparison of graph
  embedding methods in practical tasks.** Physical Review E 104:044315.
  arXiv:2106.10198. https://arxiv.org/abs/2106.10198 **[in-code** — note: cited in
  `graph_embed.py` as "Physica A"; it is Phys. Rev. E**]**
- Narayanan, A., et al. (2017). **graph2vec: Learning Distributed Representations of
  Graphs.** arXiv:1707.05005. https://arxiv.org/abs/1707.05005
- Bagrow, J.P., Bollt, E.M. (2019). **An information-theoretic, all-scales approach to
  comparing networks.** Applied Network Science 4:45. doi:10.1007/s41109-019-0156-x
  (network portrait divergence).
- Tantardini, M., Ieva, F., Tajoli, L., Piccardi, C. (2019). **Comparing methods for
  comparing networks.** Scientific Reports 9:17557. doi:10.1038/s41598-019-53708-y.
  https://www.nature.com/articles/s41598-019-53708-y — graphlet-based distances win at
  classification; the menu for a principled "grapples most like".
- Ahmed, N., et al. (2021). **Deep graph similarity learning: a survey.** Data Mining
  and Knowledge Discovery 35:688–725. arXiv:1912.11615. https://arxiv.org/abs/1912.11615
- **Learning football player features using graph embeddings for player recommendation
  system** (2022). ACM SAC '22. doi:10.1145/3477314.3507257.
  https://dl.acm.org/doi/10.1145/3477314.3507257 — node2vec + GraphWave for player
  similarity/recommendation; the closest published analogue of "grapple-like".
- Donnat, C., Zitnik, M., Hallac, D., Leskovec, J. (2018). **Learning Structural Node
  Embeddings via Diffusion Wavelets** (GraphWave). KDD '18. arXiv:1710.10321.
- Reference papers already in `docs/graph_analysis_approaches.md` **[in-code]**:
  NBA2Vec arXiv:2302.13386 · baller2vec arXiv:2102.03291 · RisingBALLER
  arXiv:2410.00943 · Transition Network Analysis arXiv:2411.15486.

## G. Small-sample statistics

- Efron, B., Morris, C. (1975). **Data Analysis Using Stein's Estimator and its
  Generalizations.** JASA 70(350):311–319 — the baseball batting-average shrinkage
  classic; the exact shape of our per-node small-N rating problem.
- Brown, L.D. (2008). **In-season prediction of batting averages: A field test of
  empirical Bayes and Bayes methodologies.** Annals of Applied Statistics 2(1):113–152.
  arXiv:0803.3697. https://arxiv.org/abs/0803.3697
- Whelan, J.T., Klein, C.J. (2018/2021). **Improving pairwise comparison models using
  Empirical Bayes shrinkage.** arXiv:1807.09236. https://arxiv.org/pdf/1807.09236
  — shrinkage applied directly to paired-comparison (Bradley-Terry-class) ratings.
- Hennig, C. (2007). **Cluster-wise assessment of cluster stability.** Computational
  Statistics & Data Analysis 52:258–271. doi:10.1016/j.csda.2006.11.025 **[in-code**,
  `analysis/archetype.py`**]**
- Lin, J. (1991). **Divergence measures based on the Shannon entropy.** IEEE Trans. Inf.
  Theory 37(1):145–151 · Endres, D.M., Schindelin, J.E. (2003). **A new metric for
  probability distributions.** IEEE Trans. Inf. Theory 49(7):1858–1860. **[in-code**,
  `analysis/match_deviance.py`**]**
- Lundberg, S., Lee, S.-I. (2017). **A Unified Approach to Interpreting Model
  Predictions** (SHAP). NIPS 2017. arXiv:1705.07874. https://arxiv.org/abs/1705.07874
- Terner, Z., Franks, A. (2021). **Modeling Player and Team Performance in Basketball.**
  Annual Review of Statistics and Its Application 8:1–27.
  doi:10.1146/annurev-statistics-040720-015536, arXiv:2007.10550.
  https://arxiv.org/abs/2007.10550 **[in-code**, `analysis/metric_evaluation.py`**]**

## H. Tournament and bracket design

- Schwenk, A.J. (2000). **What Is the Correct Way to Seed a Knockout Tournament?**
  American Mathematical Monthly 107(2):140–150. doi:10.1080/00029890.2000.12005171.
- Appleton, D.R. (1995). **May the best man win?** J. Royal Statistical Society D
  44(4):529–538.
- Csató, L. (2017). **A new knockout tournament seeding method and its axiomatic
  justification.** Operations Research Letters / S.I.
  https://www.sciencedirect.com/science/article/abs/pii/S0167637716300876
- Prince, M., Cole Smith, J., Geunes, J. (2013). **Designing fair 8- and 16-team
  knockout tournaments.** IMA Journal of Management Mathematics 24(3):321–336.
  https://www.researchgate.net/publication/266160964
- Hennessy, J., Glickman, M. (2016). **Bayesian optimal design of fixed knockout
  tournament brackets.** Journal of Quantitative Analysis in Sports 12(1):1–15.
  doi:10.1515/jqas-2015-0033.
- Brandes, U., Marmulla, G., Smokovic, I. (2025). **Efficient computation of tournament
  winning probabilities.** Journal of Sports Analytics. doi:10.1177/22150218251313905.
  https://journals.sagepub.com/doi/10.1177/22150218251313905 — exact advancement
  probabilities by dynamic programming; no Monte Carlo needed at bracket sizes like 16.
- **Using Conformal Win Probability to Predict the Winners of the Cancelled 2020 NCAA
  Basketball Tournaments** (2022). arXiv:2208.08598. https://arxiv.org/pdf/2208.08598
  — win probabilities with finite-sample validity guarantees (conformal prediction).
- **A Four-Section Bracket for the 48-team World Cup** (2026). arXiv:2606.19554.

## I. Combat-sports computer vision

- ViCoS Lab, University of Ljubljana. **Brazilian Jiu-Jitsu Positions Dataset.**
  https://vicos.si/resources/jiujitsu/ — 120,279 images, 10 positions × top/bottom = 18
  classes, COCO-17 keypoints **[in-code**, `cv/`**]**; companion paper: *Video-Based
  Detection of Combat Positions and Automatic Scoring in Jiu-jitsu*.
- Yan, S., Xiong, Y., Lin, D. (2018). **Spatial Temporal Graph Convolutional Networks
  for Skeleton-Based Action Recognition** (ST-GCN). AAAI 2018. arXiv:1801.07455.
  https://arxiv.org/abs/1801.07455
- **Deep spatio-temporal graph convolutional network for police combat action
  recognition and training assessment** (2025). Scientific Reports.
  https://www.nature.com/articles/s41598-025-26405-2 — ST-GCN applied to combat actions,
  96.7% over 12 classes; evidence the method transfers to two-body combat domains.
- **Skeleton Based Graph Convolutional Network Method for Action Recognition in Sports:
  A Review** (2023). IEEE. https://ieeexplore.ieee.org/document/10390711/
- Kipf, T., Welling, M. (2017). **Semi-Supervised Classification with Graph
  Convolutional Networks.** ICLR 2017. arXiv:1609.02907. **[in-code**,
  `analysis/gnn_predictor.py`'s actual primary source**]**
- Hamilton, W.L., Ying, R., Leskovec, J. (2017). **Inductive Representation Learning on
  Large Graphs.** NIPS 2017. arXiv:1706.02216. https://arxiv.org/abs/1706.02216
  **[in-code**, `analysis/poc/e13_graphsage.py`**]** — GraphSAGE. Learns an *aggregation
  function* over a node's neighbourhood instead of one embedding per node, which is what
  makes it apply to graphs never seen in training; the PPI *multi-graph* experiment (train
  on some graphs, generalise to entirely unseen ones) is the protocol PoC-E13 copies onto
  per-athlete ActionFlow graphs. The direct contrast with `analysis/embeddings.py`, which
  is transductive by construction: one fixed vector per canonical label, none at all for a
  label the library has not seen.
- Zhang, M., Chen, Y. (2018). **Link Prediction Based on Graph Neural Networks.** NIPS
  2018 (spotlight). arXiv:1802.09691. https://arxiv.org/abs/1802.09691 **[in-code**,
  `analysis/poc/e13_graphsage.py`**]** — SEAL. Its γ-decaying heuristic theory shows a
  local enclosing subgraph approximates a wide family of heuristics, and the corollary is
  the one PoC-E13 pre-registered against itself: an encoder that computes each node's
  embedding independently of the target pair cannot represent a pair-specific structural
  feature. Cited BEFORE the run as the reason its model class is the weak form; the run's
  REJECT is consistent with the prediction.
- Ma, W., Wang, Y., Wang, X., Zhang, M. (2024). **Reconsidering the Performance of GAE in
  Link Prediction.** CIKM 2025. arXiv:2411.03845. https://arxiv.org/abs/2411.03845 — a
  well-tuned graph autoencoder matches recent sophisticated models (SOTA on ogbl-ppa) at a
  fraction of the cost; the methodological point is that link-prediction progress is
  measured against baselines nobody bothered to tune. It is why PoC-E13's verdict turns on
  a no-aggregation ablation trained with the same budget rather than on a heuristic table.
- Kumar, A., et al. (2025). **A comprehensive survey on link prediction: from heuristics
  to graph transformers.** The Journal of Supercomputing. doi:10.1007/s11227-025-07882-8.
  https://link.springer.com/article/10.1007/s11227-025-07882-8 — current survey of the
  task, its evaluation protocol and the accuracy/scalability/interpretability trade-offs;
  its practical recommendation (calibrated heuristic baselines first, GNNs when attributes
  are informative) is the shape of PoC-E13's comparator set.

## J. BJJ / grappling sports science

- Andreato, L.V., et al. (2016). **Physical and Physiological Profiles of Brazilian
  Jiu-Jitsu Athletes: a Systematic Review.** Sports Medicine – Open 3:9.
  doi:10.1186/s40798-016-0069-5. https://link.springer.com/article/10.1186/s40798-016-0069-5
- Andreato, L.V., Follmer, B., et al. (2016). **Brazilian Jiu-Jitsu Combat Among
  Different Categories: Time-Motion and Physiology. A Systematic Review.** Strength &
  Conditioning Journal 38(6):44–54. doi:10.1519/SSC.0000000000000256.
  — effort:pause 6:1–13:1; the time-structure priors for any tempo/momentum metric.
- Andreato, L.V., et al. (2015). **Brazilian Jiu-Jitsu Simulated Competition Part II:
  Physical Performance, Time-Motion, Technical-Tactical Analyses, and Perceptual
  Responses.** J. Strength & Conditioning Research 29(7):2015–2025. PMID 25559902.
  — published attempt/success rates per technique family (e.g. armbar 34% of submission
  attempts; scissor sweep 55% success): external anchors for our per-family rates.
- **Physical performance, time-motion, technical-tactical analyses, and perceptual
  responses in Brazilian jiu-jitsu matches of varied duration.** Kinesiology 51(1).
  https://ojs.srce.hr/kinesiology/article/view/5331
- **Position before submission? Techniques and tactics in competitive no-gi Brazilian
  jiu-jitsu.** Revista de Artes Marciales Asiáticas.
  https://revistas.unileon.es/index.php/artesmarciales/article/view/7410 — no-gi
  competition technique frequencies; a second external anchor, no-gi specific.

## K. MMA outcome prediction

- **Data-Driven MMA Outcome Prediction Enhanced by Fighter Styles: A Machine Learning
  Approach** (2024). https://www.researchgate.net/publication/384178666 — style features
  measurably improve outcome prediction; supports the archetype→prediction PoC.
- **Artificial Intelligence in UFC Outcome Prediction and Fighter Strategies
  Optimization** (2024). ACM ICIIP '24. doi:10.1145/3696952.3696966.
  https://dl.acm.org/doi/10.1145/3696952.3696966 — 79.25% accuracy / 0.887 ROC-AUC with
  63 differential features on 6,000 fights; the accuracy ceiling to calibrate
  expectations against (and a caution: earlier SOTA was 65–66%).

## L. Modern / experimental methods (PoC candidates)

- Wang, Z., Veličković, P., Hennes, D., et al. (2024). **TacticAI: an AI assistant for
  football tactics.** Nature Communications 15:1906. doi:10.1038/s41467-024-45965-x.
  https://www.nature.com/articles/s41467-024-45965-x — GNN + geometric deep learning +
  guided generation; the template for "suggest the counter" from position graphs.
- Rossi, E., et al. (2020). **Temporal Graph Networks for Deep Learning on Dynamic
  Graphs.** arXiv:2006.10637. https://arxiv.org/abs/2006.10637
- Drexler (2024). **Sports Analytics with Graph Neural Networks and Graph Convolutional
  Networks.** Preprints.org 202410.0046.
  https://www.preprints.org/manuscript/202410.0046/v1 **[in-code**,
  `analysis/gnn_predictor.py` — non-peer-reviewed review; kept for provenance, Kipf &
  Welling is the primary source**]**
- **A Universal Dense Football Event Representation Based on TabTransformer** (2026).
  arXiv:2606.09327. https://arxiv.org/pdf/2606.09327 — transformer event-stream
  representations; the sequence-model alternative to Markov chains on our event corpus.

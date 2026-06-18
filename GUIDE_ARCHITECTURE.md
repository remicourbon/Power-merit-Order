# Guide d'architecture — power-merit-order

Ce document décrit l'architecture du projet module par module, le flux de
données, la formulation des deux problèmes (merit order et LP batterie), les
invariants vérifiés par les tests, et des recettes concrètes pour modifier le
modèle. Il est écrit pour qu'on puisse réexpliquer chaque décision de conception
à l'oral, sans rien laisser dans une zone d'ombre.

Le principe directeur est le même que pour le projet crude : **toute la donnée
est dans des fichiers YAML, validée au chargement ; le `core/` ne contient que
des fonctions pures sur des dataclasses gelées ; l'UI ne contient aucune logique
métier.**

---

## 1. Vue d'ensemble

Le projet répond à une question : *étant donné un parc de production, des prix de
combustibles et un prix du carbone, à quoi ressemble le prix horaire de
l'électricité — et combien une batterie peut-elle gagner en déplaçant de
l'énergie des heures creuses (midi solaire) vers la pointe du soir ?*

Le modèle a **deux couches** :

1. **Le moteur de prix** (merit order) : pour chaque heure, on empile les unités
   par coût marginal croissant et on les appelle jusqu'à couvrir la demande. La
   dernière unité appelée fixe le prix (tarification *pay-as-clear*). En bouclant
   sur 24 heures on obtient la courbe de prix `P(t)`.
2. **L'arbitrage batterie** : un programme linéaire (LP) qui charge quand `P(t)`
   est bas et décharge quand il est haut, sous contraintes de puissance,
   d'énergie et de rendement.

La courbe `P(t)` est le point de jonction : la sortie du moteur que la couche
batterie consomme.

```
fuels, CO2, parc, profils
        │
        ▼
  marginal_cost ──► dispatch (merit order) ──► price_curve P(t) ──► battery (LP)
   SRMC / unité      prix de clearing =          le moteur          charge/décharge
                     SRMC de l'unité marginale    horaire            + P&L
```

---

## 2. Arborescence

```
data/        YAML uniquement — fuels, units, profiles, systems, batteries
core/
  data_models.py   dataclasses gelées + loaders + intégrité référentielle
  marginal_cost.py SRMC ajusté du carbone, prix de bascule charbon<->gaz
  dispatch.py      clearing merit order : unité marginale, prix, rente
  profiles.py      profils normalisés -> MW horaires (demande, solaire, éolien)
  price_curve.py   boucle le dispatch sur la journée -> P(t)
  battery.py       le LP d'arbitrage batterie (PuLP)
  system.py        orchestrateur : système + scénario -> courbe -> batterie
tests/       51 tests unitaires ; nombres de référence calculés à la main
app/         Streamlit, 3 pages — aucune logique métier
```

---

## 3. Dépendances entre modules

Le graphe de dépendances est **acyclique** et va toujours du bas niveau
(données) vers le haut niveau (orchestration). C'est ce qui rend chaque module
testable isolément.

```
data_models  ◄── marginal_cost ◄── dispatch ◄── price_curve ◄── system
     ▲                                  ▲             ▲            │
     │                                  │             │            ▼
     └────────────── profiles ──────────┘             └───────── battery
```

- `data_models` ne dépend de rien (sauf `pyyaml`).
- `marginal_cost` dépend uniquement de `data_models` (les dataclasses `Unit`,
  `Fuel`).
- `dispatch` dépend de `marginal_cost` (pour calculer les SRMC à empiler).
- `profiles` dépend de `data_models` (transforme les profils en MW).
- `price_curve` dépend de `dispatch` et `profiles` (boucle horaire).
- `battery` ne dépend que de `pulp` — il reçoit une simple liste de prix, il
  ignore d'où elle vient.
- `system` orchestre tout : il assemble `price_curve` puis `battery`.

**Pourquoi `battery` est isolé** : c'est volontaire. Le LP batterie ne connaît
qu'une liste `P(t)`. On peut donc le tester avec des prix inventés (`[10, 10,
50, 50]`) et raisonner sur son optimum sans jamais lancer le moteur de prix.

---

## 4. La couche de données (`data_models.py`)

### 4.1 Principe : valider au chargement, pas au solve

Toute donnée statique est dans `data/*.yaml`. Au chargement, chaque entrée est
instanciée dans une dataclass gelée dont le `__post_init__` valide tous les
champs. **Un YAML cassé doit être diagnosticable en quelques secondes** : chaque
message d'erreur porte le fichier et la clé, au format `[fichier] 'clé': message`.

```python
class DataValidationError(Exception): ...

def _fail(file, key, msg):
    raise DataValidationError(f"[{file}] '{key}': {msg}")
```

### 4.2 Où vit chaque grandeur physique

Décision de conception centrale, à défendre :

- Le **facteur d'émission** (`emission_factor_tco2_mwh_th`) est porté par le
  **combustible** (`Fuel`). C'est une propriété chimique : brûler une MWh
  thermique de gaz émet toujours ~0,20 tCO₂, quelle que soit la centrale.
- L'**efficacité** (`efficiency`) et l'**O&M variable** (`variable_om`) sont
  portés par l'**unité** (`Unit`). Ce sont des propriétés d'ingénierie de la
  centrale.

Cette séparation fait que deux centrales gaz (CCGT et OCGT) partagent un seul
facteur d'émission et ne diffèrent que par leur efficacité — exactement comme un
assay de brut porte le soufre tandis que la raffinerie porte la spec.

### 4.3 Immutabilité profonde

Les dataclasses sont `frozen=True`. Les mappings imbriqués (`capacities_gw`,
`values`) sont gelés via `MappingProxyType` / `tuple` et posés avec
`object.__setattr__` dans le `__post_init__`. Conséquence importante : la
`Dataset` n'est **pas picklable**, donc l'app la met en cache avec
`@st.cache_resource` (singleton, pas de sérialisation), **pas** `@st.cache_data`.

### 4.4 Intégrité référentielle inter-fichiers

`Dataset.validate_referential_integrity()` vérifie les liens entre fichiers et
liste **tous** les manques d'un coup :

- chaque `unit.fuel` existe dans `fuels.yaml` ;
- chaque technologie d'un `system.capacities_gw` existe dans `units.yaml` ;
- chaque profil référencé par un système existe **et a le bon `kind`** (le slot
  `demand_profile` doit pointer vers un profil de `kind: demand`, etc.) ;
- un système qui déclare une capacité solaire/éolienne doit avoir l'unité
  correspondante.

Point unique d'entrée : `load_dataset(data_dir)` charge les cinq fichiers puis
appelle la validation. Tout le reste du code utilise cette fonction.

---

## 5. Le coût marginal (`marginal_cost.py`)

### 5.1 Formule (à défendre verbatim)

```
SRMC = (P_fuel + EF × P_CO2) / η + VOM
```

Le coût du combustible et le coût du carbone sont additionnés au **niveau
thermique** (par MWh_th), puis divisés par l'efficacité `η` pour exprimer le
résultat par MWh **électrique**. C'est l'analogue power d'un *spark spread* :
une unité tourne dès que le prix de marché dépasse son SRMC.

Pour les renouvelables : `η = 1`, combustible `none` (prix 0, EF 0) → SRMC ≈ 0.
La formule reste uniforme sur tout le parc.

SRMC par défaut (CO₂ 75 €/t) : nucléaire ≈ 7, solaire/éolien 0, gaz CCGT ≈ 93,
charbon ≈ 97, gaz OCGT ≈ 135 €/MWh.

### 5.2 Le prix de bascule charbon ↔ gaz

`fuel_switching_co2_price(unit_a, fuel_a, unit_b, fuel_b)` résout
`SRMC_a(p) = SRMC_b(p)` pour le prix du CO₂ `p` :

```
p = [ (Pf_b/η_b + VOM_b) − (Pf_a/η_a + VOM_a) ] / [ EF_a/η_a − EF_b/η_b ]
```

Retourne `None` si les deux droites de SRMC sont parallèles en CO₂ (même
intensité carbone par MWh_e) : pas de croisement. Pour les valeurs par défaut, la
bascule se situe ≈ 67 €/t : en-dessous le charbon est moins cher, au-dessus le
gaz passe devant. C'est le « switching point » carbone, visible en direct via le
slider CO₂.

---

## 6. Le clearing merit order (`dispatch.py`)

`clear_market(units, fuels, co2, demand_mw, available_mw, value_of_lost_load)` :

1. calcule le SRMC de chaque unité et les trie par ordre croissant
   (`merit_order`) ;
2. appelle les unités dans l'ordre jusqu'à couvrir la demande ;
3. la dernière unité appelée est l'**unité marginale**, son SRMC est le **prix de
   clearing** ;
4. si la demande dépasse toute la capacité disponible, le marché est en pénurie :
   le prix passe à `value_of_lost_load` (3000 €/MWh par défaut) et le déficit est
   reporté dans `unserved_mw`.

**Le prix de clearing est exactement le dual de la contrainte d'équilibre
offre-demande** du LP de dispatch équivalent : le coût marginal de servir une MWh
de plus. On le calcule par tri plutôt que par LP, parce que pour ce problème à
une seule contrainte le tri **est** le dual exact, et c'est bien plus facile à
défendre au tableau. (Le problème batterie, lui, a des contraintes
inter-temporelles et passe donc par un vrai LP.)

La **rente inframarginale** `Σ (prix − SRMC) × MW` est l'analogue power du
GPW/netback : le prix est fixé à la marge, la valeur se lit unité par unité.

`DispatchResult` est une dataclass gelée : `clearing_price`, `marginal_unit`,
`dispatch` (MW par techno), `unserved_mw`, `inframarginal_rent`.

---

## 7. Des profils aux MW (`profiles.py`)

C'est le seul module qui connaît la **sémantique différenciée** des profils :

- profils de `kind: demand` → des **fractions de la pointe** dans `[0, 1]`,
  multipliées par `peak_demand_gw` ;
- profils de `kind: solar`/`wind` → des **facteurs de charge** dans `[0, 1]`,
  multipliés par la capacité installée pour donner les MW disponibles à l'heure.

`available_mw(system, units, solar_cf, wind_cf)` construit, pour une heure, la
capacité utilisable de chaque techno : les unités `firm` à leur nominal, le
solaire et l'éolien mis à l'échelle par leur facteur de charge.

---

## 8. Le moteur : la courbe de prix (`price_curve.py`)

`compute_price_curve(...)` boucle sur les 24 heures :

1. construit la capacité disponible de chaque techno (renouvelables au facteur de
   charge de l'heure, unités firm au nominal) ;
2. clear le marché sur la demande de l'heure ;
3. enregistre un `HourResult` (heure, demande, prix, unité marginale, MW
   renouvelables, demande résiduelle, MW non servis, dispatch).

À midi le solaire écrase la demande résiduelle, l'unité marginale descend dans la
pile, le prix chute ; le soir le solaire disparaît, le prix remonte vers un
peaker gaz. C'est le mécanisme derrière la **« duck curve »**. Cette `P(t)` est
ce que la batterie arbitre.

---

## 9. Le LP batterie (`battery.py`)

### 9.1 Formulation (version tableau, à défendre verbatim)

```
Variables (par heure t, Δt = 1 h)
  c_t ≥ 0           charge, MW
  d_t ≥ 0           décharge, MW
  soc_t ∈ [0, E]    état de charge en FIN d'heure t, MWh

Objectif (€ sur l'horizon)
  max  Σ_t  P(t) · (d_t − c_t) · Δt

Contraintes                                              nom
  soc_t = soc_{t-1} + η_c·c_t·Δt − d_t/η_d·Δt            "soc_t"
  soc_{T-1} = soc_0   (cyclique)                         "cyclic"
  0 ≤ c_t, d_t ≤ P ;  0 ≤ soc_t ≤ E   (bornes)
```

avec `η_c = η_d = √(rendement_aller-retour)`.

### 9.2 Notes de modélisation (à défendre)

- **Pas de variable binaire nécessaire.** Comme le rendement aller-retour
  `η_rt = η_c·η_d < 1` rend la charge et la décharge simultanées strictement
  perdantes, l'optimum du LP ne fait jamais les deux à la fois — le modèle reste
  un **LP pur** (pas un MILP), exactement comme le projet crude garde le choix du
  navire **hors** du LP pour rester linéaire.
- **Le dual de la contrainte `soc_t`** est la valeur marginale d'une MWh de plus
  stockée dans la batterie à l'heure t — la « water value » du stockage. C'est
  l'analogue du dual de soufre du LP crude : un prix que le modèle **découvre**,
  pas un prix qu'on lui donne. On l'exporte heure par heure
  (`energy_value_eur_mwh`).
- **Price-taker** : la batterie ne déplace pas `P(t)`. Valable pour une petite
  batterie ; une grosse rétroagirait sur la demande résiduelle (hors périmètre).
- **Anticipation parfaite** : `P(t)` est connu à l'avance, donc l'objectif est la
  **borne supérieure théorique** du revenu d'arbitrage, pas une stratégie
  tradeable. C'est l'analogue stockage de « la courbe forward est un prix
  hedgeable, pas une prévision ».

### 9.3 Le ratio de rentabilité

Le **ratio de prix d'équilibre** est `1 / η_rt` : un cycle ne vaut le coup que si
`vente / achat` dépasse ce ratio — l'analogue stockage d'un spark spread. On
l'expose (`breakeven_spread_ratio`) à côté du ratio réellement atteint
(prix de vente moyen / prix d'achat moyen).

`BatteryResult` (gelée) : `status`, `charge_mw`, `discharge_mw`, `soc_mwh`,
`energy_value_eur_mwh`, `profit_eur`, `equivalent_cycles`,
`breakeven_spread_ratio`, prix moyens d'achat/vente, et `shadow_prices` (tous les
duals nommés).

---

## 10. L'orchestrateur (`system.py`)

Analogue de `decision.py` du projet crude.

`evaluate_system(ds, system_key, scenario, battery_key=, battery_override=,
system_override=)` :

1. applique le `Scenario` (prix du CO₂ + overrides de prix de combustible) aux
   `Fuel` via `dataclasses.replace` — un décalage parallèle des prix. Les facteurs
   d'émission ne bougent pas. Le reste du pipeline ne voit que des `Fuel`
   ordinaires et ignore l'existence des sliders ;
2. calcule la courbe de prix (`price_curve`) ;
3. arbitre cette courbe avec le LP batterie (`battery`) ;
4. retourne un `SystemResult` gelé, riche, qui est tout ce dont l'UI a besoin
   (propriétés `prices`, `avg_price`, `min_price`, `max_price`).

`system_override` (un `System` construit en direct depuis les sliders) permet au
**sandbox** d'injecter un parc personnalisé sans toucher aux fichiers de données
— exactement le motif `config_override` du sandbox Marseille côté crude.

---

## 11. Invariants vérifiés par les tests

Chaque comportement économique est épinglé par un test (`tests/`, 51 tests) :

- **data_models** : le dataset réel charge ; chaque règle de validation rejette
  une entrée cassée avec un message taggé ; les dataclasses sont immuables ;
  l'intégrité référentielle attrape une référence de combustible inconnue et un
  mauvais `kind` de profil.
- **marginal_cost** : SRMC = nombres calculés à la main ; renouvelable = 0 ;
  CO₂ plus élevé augmente le SRMC thermique mais pas le nucléaire ; la bascule
  égalise bien les deux SRMC ; cas parallèle → `None`.
- **dispatch** : l'unité marginale fixe le prix ; rente inframarginale exacte ;
  le peaker fixe le prix en pointe ; la pénurie déclenche la VOLL ; une dispo
  renouvelable faible remonte dans la pile.
- **price_curve** : 24 heures ; midi ≤ pointe du soir ; résiduel < demande quand
  les renouvelables tournent ; un CO₂ nul ne fait pas monter les prix ; un parc
  nucléaire est moins cher qu'un parc thermique.
- **battery** : achète bas / vend haut ; jamais charge+décharge simultanées ; la
  perte aller-retour érode le profit ; pas d'échange sous le seuil de rentabilité ;
  retour au SOC initial (cyclique) ; SOC dans les bornes ; les duals de SOC sont
  exportés.
- **system** : pipeline complet optimal ; les overrides de scénario changent les
  prix ; `effective_fuels` applique les overrides sans toucher aux EF ; le
  sandbox et l'override de batterie fonctionnent ; système/batterie inconnus
  lèvent une erreur.

---

## 12. Recettes de modification

### Ajouter une technologie de production
1. Ajouter le combustible dans `data/fuels.yaml` (prix + facteur d'émission) si
   nécessaire.
2. Ajouter l'unité dans `data/units.yaml` (technology, efficiency, variable_om,
   fuel, availability).
3. L'ajouter à `TECHNOLOGIES` dans `data_models.py` et lui donner un libellé +
   une couleur dans `app/app.py` (`TECH_LABEL`, `TECH_COLOR`, `TECH_ORDER`).
4. Lui donner une capacité dans les systèmes voulus (`data/systems.yaml`).

### Ajouter un système (parc)
Ajouter une entrée dans `data/systems.yaml` : `name`, les trois profils
(`demand_profile`, `solar_profile`, `wind_profile`), `peak_demand_gw`,
`capacities_gw`. La validation vérifie que profils et technos existent.

### Ajouter un profil horaire
Ajouter une entrée dans `data/profiles.yaml` avec `kind` (`demand`/`solar`/
`wind`) et 24 `values` dans `[0, 1]`. Le référencer dans un système.

### Ajouter une classe de batterie
Ajouter une entrée dans `data/batteries.yaml` : `power_mw`, `energy_mwh`,
`round_trip_pct`. Elle apparaît automatiquement dans le sélecteur de l'app.

### Changer une hypothèse de marché par défaut
Modifier les prix dans `data/fuels.yaml` (les sliders partent de ces valeurs).
Les facteurs d'émission sont des constantes physiques : on n'y touche pas.

---

## 13. Limites assumées (et la v2)

Les entrées sont stylisées **par choix**, pas par incapacité : le dispatch et le
LP batterie sont identiques que les nombres soient stylisés ou sourcés. Les
limites sont listées dans le README et sur la page « Reference data » de l'app
(tarification *pay-as-clear*, absence de contraintes inter-temporelles de
production, price-taker + anticipation parfaite pour la batterie, pas de prix
négatifs).

La v2 naturelle : injecter les capacités, la charge et la production renouvelable
réelles d'ENTSO-E et **calibrer `P(t)` contre les prix day-ahead** — mesurer
l'écart entre un merit order de manuel et les prix observés, plutôt que le
cacher. C'est l'analogue de la validation JODI/EIA du projet crude. Les prix
négatifs et la rétroaction d'une grosse batterie sur la demande résiduelle sont
des extensions ultérieures.

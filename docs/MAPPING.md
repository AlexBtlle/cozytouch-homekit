# Mapping Overkiz → HomeKit

Tableau de référence à **remplir après `explore`** avec les states réels de
votre firmware. Ne pas considérer les valeurs ci-dessous comme acquises : elles
sont des **hypothèses** à confirmer au dump.

## V1 — lecture seule (implémenté)

Chaque capteur cible son **propre** `device_url` (sous-devices Overkiz distincts).

| Feature (config) | Service HomeKit | Caractéristique | State Overkiz | `controllable_name` du device |
|---|---|---|---|---|
| `temp_ambiante`   | `TemperatureSensor` | `CurrentTemperature` | `core:TemperatureState` | `io:AtlanticPassAPCZoneTemperatureSensor` |
| `temp_exterieure` | `TemperatureSensor` | `CurrentTemperature` | `core:TemperatureState` | `io:AtlanticPassAPCOutsideTemperatureSensor` |
| `temp_ecs`        | `TemperatureSensor` | `CurrentTemperature` | `core:TargetDHWTemperatureState` ⚠ | `io:AtlanticPassAPCDHWComponent` |

> ✅ **Confirmé sur firmware `io:AtlanticPassAPC*`** (Alféa Extensa AI Duo) via
> `explore`. Sur un autre firmware, les `controllable_name`/states peuvent
> différer — refaire `explore`.
>
> ⚠️ **ECS** : ce firmware n'expose que des **consignes** DHW
> (`core:ComfortTargetDHWTemperatureState`, `core:EcoTargetDHWTemperatureState`,
> `core:TargetDHWTemperatureState`), **pas** de température mesurée du ballon.
> Le capteur `temp_ecs` reflète donc une consigne, pas une mesure.

Caractéristiques annexes ajoutées à chaque capteur :
- `StatusActive` / `StatusFault` → passent en défaut quand l'API ne répond pas
  (valeur « indisponible » plutôt qu'une température figée trompeuse).

## V2 — écriture (prévu, non implémenté)

| Fonction | Service HomeKit envisagé | Commande Overkiz (à confirmer) | Remarque |
|---|---|---|---|
| Consigne de zone | `Thermostat` ou `HeaterCooler` | `setTargetTemperature` / `setHeatingLevel` | HomeKit n'a pas de type « loi d'eau » → mapping assumé |
| Mode chauffe/arrêt | `Thermostat.TargetHeatingCoolingState` | `setOperatingMode` / `setOnOff` | — |
| Boost ECS | `Switch` | `setBoostMode` / `setDHWMode` | Ballon du Duo |

> ⚠️ Le mapping d'écriture est imparfait par nature (HomeKit ne modélise pas une
> PAC pilotée par loi d'eau). Toute décision de mapping sera documentée ici.

## Comment remplir ce tableau

```bash
python -m cozytouch_homekit explore --anonymize
```

Ouvrez `explore_dump.json`, repérez votre PAC/ECS (`device_url`, `label`) et la
liste de leurs `states`. Reportez les noms exacts dans `config.yaml`
(section `sensors`) puis relancez le service.

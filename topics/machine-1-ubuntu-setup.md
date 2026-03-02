# Machine 1 — Ubuntu Server 24.04 LTS Setup (Dual Boot)

> Issue: #6
> Statut : En cours
> Dernière mise à jour : 2026-03-02

---

## Vue d'ensemble

| Contexte | Étapes |
|----------|--------|
| 🖥 Physique (20-30 min) | Clé USB → BIOS → Installeur → 1er reboot |
| 💻 SSH depuis Machine 2 | GRUB → NVIDIA drivers → reboot → vérifications |

**Pivot SSH** : pendant l'installeur, cocher "Install OpenSSH server". Dès le 1er reboot, tout se fait depuis Machine 2.

---

## Contexte

Machine 1 = Hub central du projet Lyra.

| Spec | Valeur |
|------|--------|
| CPU | AMD Ryzen 7 5800X |
| RAM | 32GB |
| GPU | RTX 3080 10GB VRAM |
| OS cible | Ubuntu Server 24.04 LTS (dual boot Windows, défaut Linux) |

---

## Prérequis

- Clé USB ≥ 8GB
- ISO Ubuntu Server 24.04 LTS : https://ubuntu.com/download/server
- Outil de création de clé bootable : Rufus (Windows) ou `dd` (Linux)
- Machine 2 disponible pour vérifier SSH

---

## 🖥 PHYSIQUE — Étape 1 : Préparer la clé USB bootable

Peut se faire sur Machine 2 avant d'aller sur Machine 1 :

```bash
# Sur Machine 2 (Linux)
dd if=ubuntu-24.04-live-server-amd64.iso of=/dev/sdX bs=4M status=progress && sync
```

Ou via Rufus sur Windows (GPT + UEFI, pas MBR).

---

## 🖥 PHYSIQUE — Étape 2 : Installation Ubuntu (dual boot)

**Attention** : Windows doit déjà être installé et fonctionnel.

1. Brancher la clé USB sur Machine 1
2. Démarrer et appuyer sur F2/Del → boot sur la clé USB
3. Au partitionnement, choisir **"Utiliser l'espace libre"** ou **custom** :

| Partition | Taille | Type | Point de montage |
|-----------|--------|------|-----------------|
| EFI (existante Windows) | — | EFI | `/boot/efi` |
| Ubuntu root | 200GB+ | ext4 | `/` |
| Swap | 32GB (= RAM) | swap | — |
| Données | Reste | ext4 | `/data` |

**Ne pas formater** la partition EFI Windows — juste la sélectionner comme `/boot/efi`.

4. Étape **"SSH Setup"** → ✅ **Install OpenSSH server**
   - Option : importer clé depuis GitHub (`gh:<username>`) → SSH sans mot de passe dès le 1er boot

5. Terminer l'installation → reboot → retirer la clé USB

**À partir d'ici, tout se fait depuis Machine 2 via SSH.**

---

## 💻 SSH — Étape 3 : Connexion initiale depuis Machine 2

```bash
# Trouver l'IP de Machine 1 (si pas connue : regarder la console au boot, ou box routeur)
ssh mickael@<IP_MACHINE_1>
```

Si clé SSH non importée pendant l'install :

```bash
# Sur Machine 2
ssh-keygen -t ed25519 -C "machine2@lyra"
ssh-copy-id mickael@<IP_MACHINE_1>   # demande le mot de passe une dernière fois
```

---

## 💻 SSH — Étape 4 : Configuration GRUB (défaut Linux)

```bash
sudo nano /etc/default/grub
```

```ini
GRUB_DEFAULT=0          # 0 = premier entry = Ubuntu
GRUB_TIMEOUT=5          # 5s pour choisir
GRUB_TIMEOUT_STYLE=menu
GRUB_DISABLE_OS_PROBER=false   # détecter Windows
```

```bash
sudo update-grub
# Vérifier que Windows Boot Manager apparaît dans la liste
```

---

## 💻 SSH — Étape 5 : NVIDIA Drivers (RTX 3080)

```bash
# Vérifier GPU détecté
lspci | grep -i nvidia

# Installer drivers recommandés
sudo apt update
sudo ubuntu-drivers autoinstall

# OU version spécifique (recommandé pour RTX 3080 + CUDA)
sudo apt install -y nvidia-driver-550

# Reboot obligatoire
sudo reboot
```

Après reboot :

```bash
nvidia-smi
```

Sortie attendue :

```
+-----------------------------------------------------------------------------+
| NVIDIA-SMI 550.x    Driver Version: 550.x    CUDA Version: 12.x            |
|-------------------------------+----------------------+----------------------+
| GPU  Name        Persistence-M| Bus-Id        Disp.A | Volatile Uncorr. ECC |
| Fan  Temp  Perf  Pwr:Usage/Cap|         Memory-Usage | GPU-Util  Compute M. |
|===============================+======================+======================|
|   0  NVIDIA GeForce ...  Off  | 00000000:XX:00.0 Off |                  N/A |
|  0%   30C    P8     5W / 320W |      0MiB / 10240MiB |      0%      Default |
+-----------------------------------------------------------------------------+
```

---

## 💻 SSH — Étape 6 : Vérifications finales

```bash
# GPU OK
nvidia-smi

# SSH accessible depuis Machine 2
ssh mickael@<IP_MACHINE_1> "echo OK"

# GRUB défaut Linux
grep GRUB_DEFAULT /etc/default/grub

# Services au démarrage
sudo systemctl is-enabled ssh
```

---

## Acceptance criteria

- [ ] `ssh mickael@<IP_MACHINE_1>` fonctionne depuis Machine 2
- [ ] `nvidia-smi` affiche RTX 3080 (10240 MiB VRAM)
- [ ] Machine démarre sur Linux par défaut (GRUB_DEFAULT=0)
- [ ] Windows toujours accessible via GRUB (entrée Windows Boot Manager présente)

---

## Troubleshooting

### GRUB ne voit pas Windows

```bash
sudo os-prober
sudo update-grub
```

Si os-prober désactivé :

```bash
echo 'GRUB_DISABLE_OS_PROBER=false' | sudo tee -a /etc/default/grub
sudo update-grub
```

### Drivers NVIDIA — module non chargé

```bash
sudo modprobe nvidia
dmesg | grep -i nvidia
# Si Secure Boot activé, désactiver dans BIOS ou signer le module
```

### SSH — connexion refusée

```bash
# Vérifier le firewall
sudo ufw status
sudo ufw allow ssh

# Vérifier le service
sudo systemctl restart ssh
```

---

## Notes post-install

- Après configuration complète, désactiver `PasswordAuthentication yes` → mettre `no`
- Envisager `fail2ban` pour protéger SSH
- Fixer l'IP de Machine 1 en DHCP statique sur le routeur (MAC binding) pour URL SSH stable

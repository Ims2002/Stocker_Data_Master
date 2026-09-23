# Despliegue en el VPS de Hetzner (versión viva)

Alternativa a `DESPLIEGUE.md` (Streamlit Community Cloud, demo congelada).
Aquí el proyecto corre entero en el servidor: base completa de 208
tickers, pipeline por cron a las 22:30 y la web siempre al día.

Reparto: **Caddy** en los puertos 80/443 con HTTPS automático → **Streamlit**
escuchando solo en `127.0.0.1:8501` bajo **systemd** → **cron** ejecutando
`run_pipeline.py` sobre la misma `data/stocker.db`.

---

## 0. Antes de empezar

**Dominio.** `ivanms.dev` encaja bien para un perfil de datos: `.dev` es
de Google, cuesta ~12 €/año y viene con HTTPS obligatorio de fábrica
(está en la lista HSTS preload), lo cual con Caddy no te cuesta trabajo
extra. `ivanms.com` es la alternativa si quieres algo más neutro y menos
técnico, y `.es` si te interesa marcar España en las candidaturas.
Regístralo en Cloudflare (precio de coste, sin renovaciones infladas) o
Namecheap.

Luego, un registro **A** apuntando a la IP del servidor:

```
stocker.ivanms.dev.   A   91.99.102.161
```

(y un **AAAA** a la IPv6 del VPS si la tienes). Si usas Cloudflare como
DNS, deja la nube **gris** (DNS only) la primera vez: con la nube naranja
el proxy se mete por medio y Caddy no puede validar el certificado.

**Memoria.** Comprueba qué te deja libre el Minecraft:

```bash
free -h
```

El dashboard necesita entre 500 MB y 1 GB (Streamlit + los `.joblib` de
scikit-learn cargados en memoria), y el pipeline nocturno pide otro tanto
mientras construye la capa gold. Si vas justo, añade 2 GB de swap antes
de seguir:

```bash
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

**Zona horaria** (para que el cron de las 22:30 sea la hora de Madrid):

```bash
sudo timedatectl set-timezone Europe/Madrid
```

---

## 1. Preparar el servidor

Usuario propio para la app, sin privilegios (no la ejecutes como root ni
como el usuario del Minecraft):

```bash
sudo adduser --system --group --home /opt/stocker stocker
sudo apt update && sudo apt install -y python3-venv python3-pip git
```

Caddy (repositorio oficial):

```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install -y caddy
```

Cortafuegos — **el 8501 no se abre**: Streamlit solo escucha en local y
todo entra por Caddy:

```bash
sudo ufw allow OpenSSH
sudo ufw allow 80,443/tcp
sudo ufw enable
```

---

## 2. El proyecto y sus dependencias

```bash
sudo -u stocker -s
cd /opt/stocker
git clone <url-del-repo> .          # rama main, ya con mejoras-auditoria fusionada
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

`lightgbm` es opcional (solo si entrenas con `--model lightgbm`): si la
instalación se hace pesada, coméntala en `requirements.txt`.

Las claves, en `/opt/stocker/.env` (nunca en git), legible solo por su
dueño:

```bash
cp .env.example .env
nano .env        # ALPHA_VANTAGE_API_KEY=... y ANTHROPIC_API_KEY=...
chmod 600 .env
```

---

## 3. Llevar la base de datos y los modelos desde el PC

Primero, en Windows, deja la base en un estado consistente (cierra el
dashboard y espera a que no haya pipeline corriendo):

```bat
python src\maintenance.py backup
python src\maintenance.py check
```

`backup` deja una copia limpia en `data\backups\` — esa es la que
conviene enviar, no el `.db` en caliente con sus `-wal`/`-shm` al lado.
Desde PowerShell (el `scp` de Windows ya viene con OpenSSH):

```powershell
scp "C:\Users\imsmo\Documents\Claude Projects\stocker_project_v2\data\backups\<copia>.db" `
    stocker@91.99.102.161:/opt/stocker/data/stocker.db

scp "C:\Users\imsmo\Documents\Claude Projects\stocker_project_v2\models\random_forest_h1_20260916180523.joblib" `
    "C:\Users\imsmo\Documents\Claude Projects\stocker_project_v2\models\random_forest_h5_20260916180645.joblib" `
    "C:\Users\imsmo\Documents\Claude Projects\stocker_project_v2\models\random_forest_h20_20260916181145.joblib" `
    stocker@91.99.102.161:/opt/stocker/models/
```

Son ~270 MB de base: unos minutos. Ya en el servidor, verifica que llegó
entera antes de seguir:

```bash
cd /opt/stocker && venv/bin/python src/maintenance.py check
```

Y una prueba a mano, que es la forma rápida de descubrir un fallo de
dependencias o de permisos:

```bash
venv/bin/streamlit run dashboard/Inicio.py --server.address 127.0.0.1 --server.port 8501 --server.headless true
curl -sI http://127.0.0.1:8501 | head -1      # en otra sesión: HTTP/1.1 200 OK
```

---

## 4. Servicio systemd

`/etc/systemd/system/stocker.service`:

```ini
[Unit]
Description=Stocker dashboard (Streamlit)
After=network-online.target

[Service]
Type=simple
User=stocker
Group=stocker
WorkingDirectory=/opt/stocker
Environment=PYTHONUTF8=1
ExecStart=/opt/stocker/venv/bin/streamlit run dashboard/Inicio.py \
  --server.address 127.0.0.1 --server.port 8501 --server.headless true \
  --browser.gatherUsageStats false
Restart=always
RestartSec=5
# El pipeline y el dashboard comparten la base; el resto del disco, de solo lectura.
ProtectSystem=full
PrivateTmp=true
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now stocker
sudo systemctl status stocker
```

---

## 5. Caddy

`/etc/caddy/Caddyfile` (sustituye el contenido de ejemplo):

```
stocker.ivanms.dev {
    reverse_proxy 127.0.0.1:8501
    encode zstd gzip
}
```

```bash
sudo systemctl reload caddy
```

Eso es todo: Caddy pide el certificado a Let's Encrypt solo, lo renueva
solo y reenvía los websockets que Streamlit necesita sin configuración
extra. Si falla, `sudo journalctl -u caddy -n 50` casi siempre dice que
el DNS todavía no ha propagado o que hay un proxy de Cloudflare delante.

---

## 6. El pipeline por cron

```bash
sudo -u stocker crontab -e
```

```cron
PYTHONUTF8=1
# Cadena diaria completa: precios, gold, predicciones (1/5/20), resolución y noticias.
# 22:30 Madrid = después del cierre de EE. UU. (hallazgo C2 de la auditoría).
30 22 * * 1-6 cd /opt/stocker && venv/bin/python src/run_pipeline.py >> data/pipeline_cron.log 2>&1

# Copia de seguridad semanal de la base (domingos a las 04:00).
0 4 * * 0 cd /opt/stocker && venv/bin/python src/maintenance.py backup >> data/maintenance.log 2>&1
```

El cerrojo de `pipeline_lock.py` ya impide que dos ejecuciones se pisen,
así que no hace falta nada más.

**Desactiva la tarea programada de Windows** el mismo día que actives
este cron. No es que corrompa nada —cada copia escribe en su propia
base— pero comparten la cuota diaria de Alpha Vantage (25 llamadas) y, a
partir de aquí, **la base buena es la del servidor**: la del PC se queda
atrás y se convierte en una copia de trabajo.

Rotación de logs (`pipeline_cron.log` ya va por 1,7 MB) —
`/etc/logrotate.d/stocker`:

```
/opt/stocker/data/*.log {
    weekly
    rotate 8
    compress
    missingok
    notifempty
    copytruncate
}
```

---

## 7. La primera noche

Lanza la cadena a mano una vez, sin esperar al cron, y mírala entera:

```bash
cd /opt/stocker && venv/bin/python src/run_pipeline.py
cat data/pipeline_status.json
```

Lo que hay que vigilar en esa primera ejecución:

- **yfinance desde una IP de centro de datos.** Yahoo limita más que
  desde una conexión doméstica. Si ves descargas vacías o errores de
  *rate limit*, el propio `run_pipeline.py` lo deja en el log y en
  `pipeline_status.json`; la salida es espaciar los lotes en
  `download.py`, no insistir.
- **Memoria.** `free -h` mientras corre la capa gold. Si el kernel mata
  el proceso (`journalctl -k | grep -i oom`), el swap del punto 0 es la
  solución barata.
- **Los seis pasos con `rc=0`** en `pipeline_status.json`. El dashboard
  lo enseña en "Día a día".

---

## 8. Actualizar y mantener

```bash
sudo -u stocker -s -c 'cd /opt/stocker && git pull'
sudo systemctl restart stocker
```

Reentrenar no es diario: cuando te apetezca aprovechar el histórico nuevo,

```bash
cd /opt/stocker
for h in 1 5 20; do venv/bin/python src/model.py --horizon $h; done
sudo systemctl restart stocker      # para que el dashboard cargue el .joblib nuevo
```

Y de vez en cuando, `venv/bin/python src/maintenance.py check` (y
`repair` + `vacuum` si algo chirría, igual que en local).

---

## 9. Un aviso sobre el chat Premium

Aquí sí tiene sentido dejar la `ANTHROPIC_API_KEY` en el `.env` del
servidor: no se expone a nadie y los contadores de
`dashboard/.premium_chat_usage.json` ahora persisten de verdad (disco
real, no efímero), así que los topes por sesión, por IP y globales
funcionan como fueron diseñados. Aun así, el enlace es público: pon un
límite de gasto en la consola de Anthropic y mira el JSON de vez en
cuando.

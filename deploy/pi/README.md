# Raspberry Pi 4 / reTerminal 배포

개발 PC에서 릴리스 tarball을 만들고, Pi에서 `install.sh` 한 번으로 설치합니다. 코드는 `/opt/biosim/app`, 파이썬 가상환경은 `/opt/biosim/venv`, 데이터(설정·SQLite DB·루프 은행·캡처)는 `/var/lib/biosim`에 놓이며 systemd 서비스 `biosim`으로 부팅 시 자동 실행됩니다.

## 1. 개발 PC에서 릴리스 만들기

```bash
deploy/pi/build_release.sh                 # 코드만  -> dist/biosim-pi-<날짜>.tar.gz (~1 MB)
deploy/pi/build_release.sh --with-bank     # + 루프 은행 포함 (~900 MB, Pi에서 5~10분 생성 생략)
deploy/pi/build_release.sh --version 1.0.0
```

`--with-bank`는 `deploy/pi/config.pi.json`의 신호 설정(ecg_fs 250, variants_per_rhythm 8, loop_seconds 3600, seed)과 같은 은행이 개발 PC의 `data/loops/250`에 있어야 합니다. 없으면 먼저 Pi 설정으로 생성합니다.

```bash
BIOSIM_DATA_DIR=/tmp/pibank .venv/bin/python - <<'PY'
import json, shutil, pathlib; d = pathlib.Path('/tmp/pibank'); d.mkdir(exist_ok=True)
shutil.copy('deploy/pi/config.pi.json', d / 'config.json')
PY
BIOSIM_DATA_DIR=/tmp/pibank .venv/bin/python run.py --pregen
BIOSIM_DATA_DIR=/tmp/pibank deploy/pi/build_release.sh --with-bank
```

## 2. Pi에 설치 (64-bit Raspberry Pi OS Bookworm/Bullseye)

```bash
scp dist/biosim-pi-<버전>.tar.gz pi@<pi-host>:~/
ssh pi@<pi-host>
tar xzf biosim-pi-<버전>.tar.gz
sudo biosim-pi-<버전>/install.sh              # 패키지 + venv(piwheels) + 설정 + sysctl + 서비스 시작
sudo biosim-pi-<버전>/install.sh --pregen     # 루프 은행을 지금 생성 (미포함 릴리스일 때, 5~10분)
sudo biosim-pi-<버전>/install.sh --kiosk      # reTerminal 터치 화면에 Chromium 키오스크 자동 실행
```

설치 후 GUI는 `http://<pi-ip>:8080`, API 문서는 `/api/v1` 입니다. 처음 시작 시 루프 은행이 없으면 서비스가 스스로 생성하며 대시보드에 진행률이 보입니다.

옵션(환경 변수): `BIOSIM_HOME`(기본 /opt/biosim), `BIOSIM_DATA`(기본 /var/lib/biosim), `BIOSIM_USER`(기본 biosim), `BIOSIM_PORT`(기본 8080). 8080을 다른 프로세스가 쓰고 있으면(`sudo ss -ltnp | grep 8080`) `sudo env BIOSIM_PORT=8090 ./install.sh --update`처럼 포트를 바꿔 다시 실행하면 서비스 유닛이 새 포트로 갱신됩니다. `--no-service`는 파일과 venv만 설치하고 서비스는 만들지 않습니다. `--dry-run`은 실행할 명령만 출력합니다.

## 3. 운영

```bash
systemctl status biosim            # 상태
journalctl -u biosim -f            # 로그
sudo systemctl restart biosim
/opt/biosim/app/deploy/pi/healthcheck.sh   # 실행 여부 · pkt/s · 이동 중 환자 · 게이트웨이 요약
```

업데이트는 개발 PC에서 한 줄입니다(코드만 교체, 데이터 유지):

```bash
deploy/pi/update.sh pi@<pi-host>                       # 새 릴리스 빌드 후 전송 + install.sh --update
deploy/pi/update.sh pi@<pi-host> dist/biosim-pi-1.0.0.tar.gz
```

## 4. Pi 기본 설정 (`config.pi.json`)

| 항목 | 값 | 이유 |
|---|---|---|
| general.active_patients | 500 | Pi 4 워커 3개 기준 여유 있는 시작점. 전송 탭의 자동 최적화로 올릴 수 있음 |
| general.bed_capacity | 2000 | 병원 규모·도면은 그대로 |
| general.profile_count | 4000 | 프로필 생성 시간·메모리 절약 |
| signals.ecg_fs | 250 | 500 Hz 대비 대역폭·CPU 절반 |
| signals.variants_per_rhythm | 8 | 루프 은행 크기(약 600 MB)와 생성 시간 절감 |
| transport.workers | 3 | 4코어 중 1코어는 월드/웹 서버 |
| scenario.exam_trip_ratio | 5.0 | 이동 중 환자 5 % |

첫 설치 때만 `/var/lib/biosim/config.json`으로 복사되고 이후에는 GUI에서 바꾼 값이 유지됩니다. 서비스 유닛은 `OMP_NUM_THREADS=1`로 numpy 스레드 경합을 막고 `LimitNOFILE=65535`, `/etc/sysctl.d/99-biosim.conf`(somaxconn 4096 등)로 수천 개의 게이트웨이 소켓을 허용합니다.

## 5. reTerminal 메모

* 5인치 1280×720 터치 화면: 키오스크는 `--touch-events=enabled`, 화면 보호기·DPMS 해제.
* 발열: 지속 부하 시 방열판/팬 권장. `vcgencmd measure_temp`가 80 °C에 가까우면 active_patients를 낮추세요.
* SD 카드 수명: 캡처(`/var/lib/biosim/runtime/capture`)는 드릴 때만 켜고, 필요하면 `BIOSIM_DATA`를 USB SSD로 지정하세요.

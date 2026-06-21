# starlink-TCP

Starlink 환경에서 TCP/UDP 성능을 측정하고, LEO handover 상황에서의 네트워크 품질 변화를 분석하기 위한 실험 프레임워크입니다.  
이 저장소는 **실험 자동화 스크립트(bin)**, **분석/시각화 도구(graph)**, 그리고 **커널 TCP 수정 코드(cca)**를 함께 포함합니다.

---

## 1. 프로젝트 목적

- Starlink 경로에서 TCP/UDP 전송 성능(throughput, RTT, loss, jitter) 측정
- POP 응답 간격(pop interval)을 handover proxy로 활용한 분석
- CUBIC/BBR 및 실험용 rwnd 제어 로직 비교
- 반복 실험을 자동화하고 결과를 일관된 형식으로 저장/시각화

---

## 2. 저장소 구조

```text
starlink-TCP/
├─ bin/                         # 실험 실행/모니터링 자동화 스크립트
│  ├─ app_layer_rtt/
│  │  ├─ tcp_ping_receiver.c
│  │  └─ udp_ping_receiver.c
│  ├─ collect_meta.sh
│  ├─ get_pop_ip.sh
│  ├─ pop_interval.sh
│  ├─ run_baseline_suite.sh
│  ├─ run_experiment.sh
│  ├─ run_iperf.sh
│  ├─ run_ping.sh
│  ├─ start_monitors.sh
│  ├─ stop_monitors.sh
│  └─ sync_time_check.sh
├─ graph/                       # 로그 분석/통계/그래프 생성
│  ├─ iperf.py
│  ├─ iperf_jsh.py
│  ├─ avg_iperf_throughput.py
│  ├─ avg_iperf_timeseries.py
│  ├─ cdf.py
│  ├─ overlay.py
│  ├─ combined_ping_throughput.py
│  ├─ ping.py
│  ├─ pop_ping.py
│  ├─ pop_ping_interval.py
│  ├─ tcpinfo.py
│  ├─ tcpinfo_jsh.py
│  ├─ chunked_iperf.py
│  └─ avg_iperf_throughput_timeseries.txt
├─ cca/                         # 커널 TCP 실험용 수정 코드
│  ├─ ipv4.h
│  ├─ sysctl_net_ipv4.c
│  └─ tcp_output_modified.c
├─ config/
└─ README.md
```

---

## 3. 실험 실행 파이프라인 (bin)

### 3.1 메인 실행: `bin/run_experiment.sh`
사용법:
```bash
bash bin/run_experiment.sh <protocol:tcp|udp|http> <cc> <direction:downlink|uplink> <flows> <run_id>
```

동작 순서:
1. 실험 ID/출력 폴더 생성
2. `collect_meta.sh`로 메타데이터 저장
3. `sync_time_check.sh`로 시간 동기 상태 기록
4. `start_monitors.sh`로 모니터링 시작
5. `run_iperf.sh`(또는 HTTP probe) 실행
6. `stop_monitors.sh`로 모니터 종료
7. `graph/*.py` 후처리 그래프 생성

---

### 3.2 배치 실행: `bin/run_baseline_suite.sh`
- 3회 반복 baseline 실험 자동 실행
- 각 반복에서:
  - TCP CUBIC downlink
  - TCP BBR downlink
  - UDP downlink (`UDP_RATE=100M`)

---

### 3.3 트래픽 실행: `bin/run_iperf.sh`
- TCP/UDP iperf3 실행
- TCP인 경우 congestion control(sysctl) 적용
- downlink는 `-R` 옵션 사용
- 결과를 `iperf.json`으로 저장

---

### 3.4 모니터 시작/종료
- `bin/start_monitors.sh`
  - `tcpdump` 캡처
  - `ss -tin` 주기 수집 (`ss_tcpinfo.log`)
  - 인터페이스/큐 통계 수집
  - POP ping RTT (`pop_ping.log`)
  - POP ping interval (`pop_interval.log`)
- `bin/stop_monitors.sh`
  - pid/pgid/패턴 기반으로 안전 종료

---

### 3.5 메타/유틸
- `bin/collect_meta.sh`: 실험 설정/커널/CC 정보 `meta.txt` 저장
- `bin/sync_time_check.sh`: `timedatectl`, `chronyc` 등 시간 동기 점검
- `bin/get_pop_ip.sh`: traceroute로 POP IP 탐색
- `bin/pop_interval.sh`: ping 응답 간격 시계열 생성
- `bin/run_ping.sh`: `ss_tcpinfo.log`를 정제해 `ss_rtt.log` 생성(현재 모니터에서 주석 비활성 가능)

---

## 4. 앱 레이어 RTT 도구 (`bin/app_layer_rtt`)

### `tcp_ping_receiver.c`
- TCP 에코 수신기(클라이언트 방식)
- 고정 패킷(`magic`, `seq`, `send_ns`)을 수신 후 그대로 echo
- `TCP_NODELAY` 적용, local bind IP 지정 가능

### `udp_ping_receiver.c`
- UDP 에코 수신기
- 시작 시 서버로 `REGISTER` 송신 후 echo 응답
- `PING_MAGIC` 검사 후 패킷 반사
- 현재 구현은 local bind 인자를 받지만 실제 bind는 `INADDR_ANY`

---

## 5. 분석/시각화 스크립트 (`graph`)

### 5.1 iperf 계열
- `iperf.py`: aggregate/per-flow throughput 그래프 + CSV
- `iperf_jsh.py`: 단일 iperf 시계열/요약
- `avg_iperf_throughput.py`: 여러 실험의 평균 throughput 및 공분산
- `avg_iperf_timeseries.py`: 1초 bin 기준 다중 실험 평균 시계열

### 5.2 CDF/오버레이/상관분석
- `cdf.py`: throughput/RTT/interval CDF, multi-dataset overlay, handover-aligned 모드
- `overlay.py`: ss 기반 throughput + pop interval 동시 플롯
- `combined_ping_throughput.py`: pop interval vs ss throughput/RTT/cwnd 상관분석

### 5.3 RTT/POP/tcpinfo
- `ping.py`: `ss_rtt.log` RTT 시각화
- `pop_ping.py`: POP RTT 시계열
- `pop_ping_interval.py`: POP 응답 간격 시계열
- `tcpinfo.py`, `tcpinfo_jsh.py`: cwnd/RTT/flow별 TCP metric 분석
- `chunked_iperf.py`: chunk 실행 결과 병합, throughput/loss/jitter 요약

---

## 6. 대표 출력 파일

실험 디렉토리(`<OUT_DIR>`)에 보통 다음이 생성됩니다.

- 원본 로그:
  - `iperf.json`, `ss_tcpinfo.log`, `pop_ping.log`, `pop_interval.log`, `ue_tcpdump.pcap`
- 메타:
  - `meta.txt`, `time_sync.txt`
- 그래프/요약:
  - `iperf3.png`, `iperf3_flows.png`, `iperf3_flows.csv`
  - `pop_interval.png`, `pop_ping_rtt.png`
  - `cwnd.png`, `tcp_rtt.png` 등

---

## 7. 예시 실행

### 단일 실험
```bash
bash bin/run_experiment.sh tcp cubic downlink 1 run1
```

### baseline suite
```bash
bash bin/run_baseline_suite.sh
```

### 개별 분석
```bash
python3 graph/avg_iperf_timeseries.py <exp1> <exp2> <exp3>
python3 graph/cdf.py \
  --dataset "BBR normal:./logs:n" \
  --dataset "BBR rwnd:./logs:" \
  --out-prefix bbr_compare
```

---

## 8. 환경 요구사항

- Linux (ss, ip, tc, tcpdump, traceroute, ping, iperf3 사용)
- 스타링크와 연결된 클라이언트 + 원격 접속 가능한 서버
- Python 3.8+
- Python 패키지:
  - `matplotlib`
  - `numpy`
  - `pandas`

설치:
```bash
pip install matplotlib numpy pandas
```

---

## 9. 커널 수정 코드(cca) 메모

`cca/`는 실험용 TCP 수정 코드입니다.

- `ipv4.h`: `sysctl_tcp_leo_rwnd_*` 파라미터 추가
- `tcp_output_modified.c`: LEO 주기/offset/outage/recovery 기반 rwnd 제어 로직
- `sysctl_net_ipv4.c`: sysctl 연결 지점

> 운영 환경 적용 전 커널 버전 정합성, 안정성, 회귀 테스트가 필요합니다.

---

## 10. 주의사항 / 트러블슈팅

- 권한 이슈: `tcpdump`, 고주기 ping은 sudo 필요
- 로그 누락: `Missing ...` 경고 시 파일명/경로 확인
- 데이터 부족: 실험 시간/샘플 수가 너무 짧으면 상관분석/CDF 품질 저하
- 인터페이스명 고정값(`enx...`) 사용 스크립트 존재 → 환경에 맞게 수정 필요

---

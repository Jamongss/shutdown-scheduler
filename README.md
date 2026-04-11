# ShutdownScheduler

Windows 시스템 트레이에서 동작하는 종료/재시작 예약 앱입니다.

## 기능

- **종료/재시작 예약** — 시간/분/초 단위로 예약 설정
- **퀵 추가 버튼** — +5분 / +30분 / +1시간 빠른 추가
- **글로벌 단축키** — `Alt+Q` 예약 창 열기, `Alt+S` 예약 취소
- **트레이 아이콘** — 백그라운드 상시 실행, 남은 시간 툴팁 표시
- **자동 시작** — Windows 로그온 시 자동 실행 등록/해제
- **단일 인스턴스** — 중복 실행 방지
- **Glass 다크 테마** — customtkinter 기반 모던 UI

## 스크린샷

> 추후 추가 예정

## 요구사항

- Windows 10 / 11
- Python 3.13
- 관리자 권한 (글로벌 단축키 후킹에 필요)

## 설치 (개발 환경)

```bash
# 의존성 설치
python3.13 -m pip install -r requirements.txt

# 실행
python3.13 shutdown_scheduler.py
```

## 빌드 (설치 파일 생성)

> Windows 환경에서 실행

```bat
build.bat
```

빌드 파이프라인:
1. 의존성 자동 설치 (PyInstaller, customtkinter, Pillow 등)
2. `generate_icon.py`로 `app_icon.ico` 생성
3. PyInstaller로 단일 `.exe` 빌드
4. Inno Setup으로 설치 파일 생성 → `dist_package\ShutdownScheduler_Setup_1.0.0.exe`

빌드 전제 조건: [Inno Setup 6](https://jrsoftware.org/isdl.php) 설치 필요

## 사용 방법

| 동작 | 방법 |
|------|------|
| 예약 창 열기 | `Alt+Q` 또는 트레이 아이콘 우클릭 → 종료 예약 |
| 예약 취소 | `Alt+S` 또는 트레이 아이콘 우클릭 → 예약 취소 |
| 자동 시작 설정 | 트레이 아이콘 우클릭 → Windows 시작 시 자동 실행 |
| 프로그램 종료 | 트레이 아이콘 우클릭 → 프로그램 종료 |

## 의존성

| 패키지 | 용도 |
|--------|------|
| `customtkinter` | 모던 UI 위젯 |
| `pystray` | 시스템 트레이 아이콘 |
| `keyboard` | 글로벌 단축키 후킹 |
| `Pillow` | 트레이 아이콘 이미지 생성 |

## 라이선스

[MIT License](LICENSE)

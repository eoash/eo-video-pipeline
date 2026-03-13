# eo-video-pipeline

EO Studio 영상 편집 자동화 파이프라인.

인터뷰 원본을 NAS에 넣으면 AI가 자동으로 분석하고, PD가 바로 편집할 수 있도록 정리해주는 시스템.

## Pipeline

```
인터뷰 원본 (3시간 × 3캠)
        ↓
  NAS 인제스트 폴더
        ↓
  프록시 자동 변환 (Compressor / Media Encoder)
        ↓
  Google Drive 동기화 (Cloud Sync)
        ↓
  Gemini 자동 분석
  ├─ 전체 Transcript
  ├─ 화자별 구간 + 주제 태깅
  ├─ 핵심 발언 하이라이트
  └─ 3캠 앵글 추천
        ↓
  Google Sheets DB 자동 저장
        ↓
  FCPXML 마커 생성 → Final Cut Pro
```

## Tech Stack

- **NAS**: Synology (Cloud Sync, Snapshot)
- **AI**: Google Gemini API (영상 분석 + Transcript)
- **Automation**: Google Apps Script
- **DB**: Google Sheets
- **Output**: FCPXML (Final Cut Pro markers)
- **Language**: Python + Apps Script (JavaScript)

## Project Structure

```
scripts/           # NAS 감시, 프록시 변환, FCPXML 생성
apps-script/       # Google Apps Script (Drive 감지 → Gemini → Sheets)
templates/         # FCPXML 템플릿
docs/              # 아키텍처 문서, PD 가이드
```

## Status

🚧 Phase 1 개발 중

## License

MIT

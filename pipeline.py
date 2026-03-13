#!/usr/bin/env python3
"""
EO Video Pipeline — 메인 오케스트레이터

인터뷰 원본 영상을 분석하여 PD가 바로 편집할 수 있도록 정리하는 파이프라인.

사용법:
  # 로컬 파일 분석
  python pipeline.py analyze /path/to/interview.mp4

  # Google Drive 폴더 감시 모드
  python pipeline.py watch

  # 특정 Drive 파일 분석
  python pipeline.py analyze --drive-id FILE_ID
"""

import argparse
import asyncio
import dataclasses
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# 프로젝트 루트를 path에 추가
sys.path.insert(0, str(Path(__file__).parent))

from src.analyzer import analyze_video, AnalysisResult
from src.fcpxml import generate_fcpxml, Marker, VideoInfo
from src.sheets import write_analysis
from src.drive import watch_folder, download_file, get_new_files


# — Marker 색상 매핑 —
EMOTION_COLORS = {
    "passionate": "red",
    "confident": "red",
    "reflective": "blue",
    "thoughtful": "blue",
    "humorous": "green",
    "excited": "orange",
    "serious": "purple",
}


def analysis_to_markers(result: AnalysisResult) -> list[Marker]:
    """분석 결과를 FCPXML 마커로 변환."""
    markers = []
    for km in result.key_moments:
        color = EMOTION_COLORS.get(km.emotion, "blue")
        # 중요도 5는 빨간색으로 강조
        if km.importance >= 5:
            color = "red"
        markers.append(Marker(
            time_seconds=km.time_seconds,
            title=f"[{km.speaker}] {km.topic}",
            note=km.quote,
            color=color,
        ))

    # 챕터 제안도 보라색 마커로 추가
    for ch in result.chapter_suggestions:
        markers.append(Marker(
            time_seconds=ch.start_seconds,
            title=f"📌 {ch.title_ko}",
            note=f"Chapter: {ch.title}",
            color="purple",
        ))

    # 시간순 정렬
    markers.sort(key=lambda m: m.time_seconds)
    return markers


async def run_analyze(video_path: str, drive_id: str | None = None):
    """단일 영상 분석 파이프라인."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("❌ GEMINI_API_KEY가 .env에 설정되지 않았습니다.")
        sys.exit(1)

    # Drive에서 다운로드
    if drive_id:
        credentials_path = os.getenv("GOOGLE_CREDENTIALS_PATH", "credentials.json")
        print(f"📥 Google Drive에서 다운로드 중... (ID: {drive_id})")
        video_path = download_file(
            file_id=drive_id,
            output_path=f"/tmp/eo_pipeline_{drive_id}",
            credentials_path=credentials_path,
        )
        print(f"✅ 다운로드 완료: {video_path}")

    if not video_path or not Path(video_path).exists():
        print(f"❌ 파일을 찾을 수 없습니다: {video_path}")
        sys.exit(1)

    video_name = Path(video_path).stem
    print(f"\n🎬 분석 시작: {video_name}")
    print(f"   파일: {video_path}")
    print(f"   시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    # Step 1: Gemini 분석
    print("🤖 Step 1/3 — Gemini 영상 분석 중...")
    result = await analyze_video(video_path, api_key)
    print(f"   ✅ Transcript: {len(result.transcript)} 글자")
    print(f"   ✅ 핵심 발언: {len(result.key_moments)}개")
    print(f"   ✅ 주제 구간: {len(result.topic_segments)}개")
    print(f"   ✅ 챕터 제안: {len(result.chapter_suggestions)}개")
    print(f"   ✅ 화자: {', '.join(result.speakers)}")
    print()

    # Step 2: Google Sheets 기록
    spreadsheet_id = os.getenv("SHEETS_SPREADSHEET_ID")
    if spreadsheet_id:
        print("📊 Step 2/3 — Google Sheets 기록 중...")
        credentials_path = os.getenv("GOOGLE_CREDENTIALS_PATH", "credentials.json")
        # sheets.py가 기대하는 키 이름으로 매핑
        analysis_dict = {
            "summary": result.summary,
            "speakers": result.speakers,
            "key_moments": [
                {
                    "timecode": km.time_seconds,
                    "speaker": km.speaker,
                    "quote": km.quote,
                    "topic": km.topic,
                    "emotion": km.emotion,
                    "importance": km.importance,
                }
                for km in result.key_moments
            ],
            "segments": [
                {
                    "start": ts.start_seconds,
                    "end": ts.end_seconds,
                    "topic": ts.topic,
                    "summary": ts.summary,
                }
                for ts in result.topic_segments
            ],
            "chapters": [
                {
                    "timecode": ch.start_seconds,
                    "title_ko": ch.title_ko,
                    "title": ch.title,
                }
                for ch in result.chapter_suggestions
            ],
        }
        sheet_url = write_analysis(
            spreadsheet_id=spreadsheet_id,
            video_name=video_name,
            analysis=analysis_dict,
            credentials_path=credentials_path,
        )
        print(f"   ✅ 시트 기록 완료: {sheet_url}")
    else:
        print("⏭️  Step 2/3 — SHEETS_SPREADSHEET_ID 미설정, 스킵")
    print()

    # Step 3: FCPXML 마커 생성
    print("🎯 Step 3/3 — FCPXML 마커 생성 중...")
    output_dir = Path(os.getenv("OUTPUT_DIR", "./output"))
    output_dir.mkdir(parents=True, exist_ok=True)

    # 영상 길이 추정 (마지막 마커 기준)
    all_times = [km.time_seconds for km in result.key_moments]
    all_times += [ch.start_seconds for ch in result.chapter_suggestions]
    estimated_duration = max(all_times) + 60 if all_times else 3600

    video_info = VideoInfo(
        filename=str(Path(video_path).resolve()),
        duration_seconds=estimated_duration,
    )
    markers = analysis_to_markers(result)
    fcpxml_path = str(output_dir / f"{video_name}_markers.fcpxml")
    generate_fcpxml(video_info, markers, fcpxml_path)
    print(f"   ✅ FCPXML 생성: {fcpxml_path}")
    print(f"   ✅ 마커 {len(markers)}개 포함")
    print()

    # Step 4: 분석 결과 JSON 저장 (백업)
    json_path = output_dir / f"{video_name}_analysis.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(dataclasses.asdict(result), f, ensure_ascii=False, indent=2)
    print(f"📄 분석 결과 JSON: {json_path}")

    # 요약 출력
    print("\n" + "=" * 60)
    print(f"✨ 분석 완료: {video_name}")
    print(f"=" * 60)
    print(f"\n📋 요약: {result.summary}")
    print(f"\n🔥 핵심 발언 TOP 5:")
    top_moments = sorted(result.key_moments, key=lambda m: m.importance, reverse=True)[:5]
    for i, km in enumerate(top_moments, 1):
        minutes = int(km.time_seconds // 60)
        seconds = int(km.time_seconds % 60)
        print(f"   {i}. [{minutes:02d}:{seconds:02d}] ({km.speaker}) \"{km.quote}\"")
    print(f"\n📌 챕터 제안:")
    for ch in result.chapter_suggestions:
        minutes = int(ch.start_seconds // 60)
        seconds = int(ch.start_seconds % 60)
        print(f"   [{minutes:02d}:{seconds:02d}] {ch.title_ko}")
    print()

    return result


async def run_watch():
    """Google Drive 폴더 감시 모드."""
    folder_id = os.getenv("DRIVE_INGEST_FOLDER_ID")
    if not folder_id:
        print("❌ DRIVE_INGEST_FOLDER_ID가 .env에 설정되지 않았습니다.")
        sys.exit(1)

    credentials_path = os.getenv("GOOGLE_CREDENTIALS_PATH", "credentials.json")
    poll_interval = int(os.getenv("POLL_INTERVAL", "60"))

    print(f"👁️  Drive 폴더 감시 시작 (폴더 ID: {folder_id})")
    print(f"   폴링 간격: {poll_interval}초")
    print(f"   새 영상이 감지되면 자동으로 분석을 시작합니다.")
    print()

    for drive_file in watch_folder(folder_id, credentials_path, poll_interval):
        print(f"\n🆕 새 영상 감지: {drive_file.name}")
        print(f"   크기: {drive_file.size / (1024**3):.1f} GB")
        print(f"   업로드: {drive_file.created_time}")

        try:
            await run_analyze(video_path=None, drive_id=drive_file.id)
        except Exception as e:
            print(f"❌ 분석 실패: {drive_file.name} — {e}")
            continue


def main():
    parser = argparse.ArgumentParser(
        description="EO Video Pipeline — 인터뷰 영상 자동 분석",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
사용 예시:
  python pipeline.py analyze interview.mp4
  python pipeline.py analyze --drive-id 1ABC...XYZ
  python pipeline.py watch
        """,
    )
    subparsers = parser.add_subparsers(dest="command", help="실행 모드")

    # analyze 명령
    analyze_parser = subparsers.add_parser("analyze", help="단일 영상 분석")
    analyze_parser.add_argument("video_path", nargs="?", help="로컬 영상 파일 경로")
    analyze_parser.add_argument("--drive-id", help="Google Drive 파일 ID")

    # watch 명령
    subparsers.add_parser("watch", help="Google Drive 폴더 감시 모드")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "analyze":
        if not args.video_path and not args.drive_id:
            print("❌ 영상 파일 경로 또는 --drive-id를 지정해주세요.")
            sys.exit(1)
        asyncio.run(run_analyze(args.video_path, args.drive_id))

    elif args.command == "watch":
        asyncio.run(run_watch())


if __name__ == "__main__":
    main()

"""Quick smoke test: generate an FCPXML with 5 sample markers."""

from fcpxml import Marker, VideoInfo, generate_fcpxml


def main() -> None:
    video = VideoInfo(
        filename="interview_final.mov",
        duration_seconds=600.0,   # 10 minutes
        framerate=23.976,
    )

    markers = [
        Marker(time_seconds=0.0,   title="Intro",           note="Opening title card",       color="blue"),
        Marker(time_seconds=32.5,  title="Question 1",      note="First interview question", color="green"),
        Marker(time_seconds=125.0, title="Key Quote",        note="Pull for social clip",     color="red"),
        Marker(time_seconds=310.8, title="B-Roll Insert",    note="Cut to product demo",      color="purple"),
        Marker(time_seconds=548.0, title="Closing Remarks",  note="Wrap-up + CTA",            color="orange"),
    ]

    output = "/tmp/test_markers.fcpxml"
    result = generate_fcpxml(video, markers, output)
    print(f"FCPXML written to: {result}")

    # Print a preview
    with open(result) as f:
        print(f.read())


if __name__ == "__main__":
    main()

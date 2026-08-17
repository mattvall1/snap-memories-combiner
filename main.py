#!/usr/bin/env python3
import argparse
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

from PIL import Image
import ffmpeg


# Directories
MEMORIES_DIR = Path("memories")
OUTPUT_DIR = Path("out")


class MemoryPair:
    """Represents a base file and its optional overlay."""

    def __init__(self, base_path: Path, overlay_path: Optional[Path] = None):
        self.base_path = base_path
        self.overlay_path = overlay_path
        self.is_video = base_path.suffix.lower() in ['.mp4', '.mov']

    def __repr__(self):
        return f"MemoryPair({self.base_path.name}, overlay={self.overlay_path.name if self.overlay_path else None})"


def scan_memories() -> List[MemoryPair]:
    """Scan the memories directory and pair base files with overlays."""
    if not MEMORIES_DIR.exists():
        print(f"Error: {MEMORIES_DIR} directory not found")
        sys.exit(1)

    # Group files by their identifier (date_uuid)
    files_by_id: Dict[str, Dict[str, Path]] = {}

    for file_path in MEMORIES_DIR.iterdir():
        if not file_path.is_file():
            continue

        # Parse filename: YYYY-MM-DD_UUID-TYPE.ext
        match = re.match(r'(.+?)-(main|overlay)\.(\w+)$', file_path.name)
        if not match:
            continue

        identifier, file_type, _ = match.groups()

        if identifier not in files_by_id:
            files_by_id[identifier] = {}

        files_by_id[identifier][file_type] = file_path

    # Create MemoryPair objects
    pairs = []
    for identifier, files in sorted(files_by_id.items()):
        if 'main' in files:
            overlay = files.get('overlay')
            pairs.append(MemoryPair(files['main'], overlay))

    return pairs


def combine_image(base_path: Path, overlay_path: Path, output_path: Path):
    """Combine base image with overlay using PIL."""
    base = Image.open(base_path)
    overlay = Image.open(overlay_path).convert("RGBA")

    # Preserve EXIF data from base image
    exif = base.info.get('exif')

    # Resize overlay to match base if needed
    if base.size != overlay.size:
        overlay = overlay.resize(base.size, Image.Resampling.LANCZOS)

    # Convert base to RGBA for compositing
    if base.mode != 'RGBA':
        base = base.convert('RGBA')

    # Composite the images
    combined = Image.alpha_composite(base, overlay)

    # Convert back to RGB for JPEG output
    if output_path.suffix.lower() in ['.jpg', '.jpeg']:
        combined = combined.convert('RGB')

    # Save with EXIF data if available
    if exif:
        combined.save(output_path, quality=95, exif=exif)
    else:
        combined.save(output_path, quality=95)

    # Copy file timestamps from original
    stat = os.stat(base_path)
    os.utime(output_path, (stat.st_atime, stat.st_mtime))


def combine_video(base_path: Path, overlay_path: Path, output_path: Path):
    """Combine base video with overlay using ffmpeg."""

    # Snapchat saves overlays as WebP with a .png extension.
    # FFmpeg detects the true WebP format but may fail to decode it,
    # so we convert it to a real PNG first using Pillow.
    with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
        tmp_overlay_path = Path(tmp.name)
    try:
        Image.open(overlay_path).convert("RGBA").save(tmp_overlay_path, format='PNG')

        input_video = ffmpeg.input(str(base_path))
        input_overlay = ffmpeg.input(str(tmp_overlay_path))

        # Get video info to scale overlay to match
        probe = ffmpeg.probe(str(base_path))
        video_info = next(s for s in probe['streams'] if s['codec_type'] == 'video')
        width = int(video_info['width'])
        height = int(video_info['height'])

        # Phones often record with the sensor in landscape and store a rotation
        # in a Display Matrix side data entry to display it upright. FFmpeg
        # auto-applies that rotation while filtering, so the frame the overlay
        # filter actually sees is width/height swapped from the raw stream info.
        rotation = next(
            (sd.get('rotation', 0) for sd in video_info.get('side_data_list', [])
             if sd.get('side_data_type') == 'Display Matrix'),
            0,
        )
        if abs(rotation) % 180 == 90:
            width, height = height, width

        # Scale overlay to match video dimensions, then overlay at 0,0
        scaled_overlay = ffmpeg.filter(input_overlay, 'scale', width, height)
        video_output = ffmpeg.filter([input_video, scaled_overlay], 'overlay', x='0', y='0')

        # Check if video has audio stream
        has_audio = any(s['codec_type'] == 'audio' for s in probe['streams'])

        # ffmpeg tags libx265 output as 'hev1' by default, which QuickTime/Apple
        # Photos refuses to play. Apple's decoders only recognize HEVC-in-MP4
        # tagged as 'hvc1'.
        creation_time = probe['format'].get('tags', {}).get('creation_time')
        metadata_kwargs = {'map_metadata': 0}
        if creation_time:
            # Snap exports (often Android-sourced) only carry the generic
            # 'creation_time' atom. Apple Photos' date importer prefers its
            # own 'com.apple.quicktime.creationdate' key and otherwise falls
            # back to the file's filesystem timestamp, so set both.
            metadata_kwargs['metadata:g'] = f'com.apple.quicktime.creationdate={creation_time}'

        if has_audio:
            audio_output = input_video.audio
            output = ffmpeg.output(video_output, audio_output, str(output_path),
                                  vcodec='libx265', acodec='copy', pix_fmt='yuv420p',
                                  crf=18, vtag='hvc1',
                                  **metadata_kwargs)
        else:
            output = ffmpeg.output(video_output, str(output_path),
                                  vcodec='libx265', pix_fmt='yuv420p',
                                  crf=18, vtag='hvc1',
                                  **metadata_kwargs)

        # Run ffmpeg
        ffmpeg.run(output, overwrite_output=True)

        # Copy file timestamps from original, so tools that ignore embedded
        # metadata (e.g. Apple Photos when it can't parse it) still see the
        # correct date.
        stat = os.stat(base_path)
        os.utime(output_path, (stat.st_atime, stat.st_mtime))
    finally:
        tmp_overlay_path.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description="Snapchat Memories Batch Combiner")
    parser.add_argument('--check-previous', action='store_true',
                         help="Skip files that already have a combined output in the out/ directory")
    parser.add_argument('--include-bases', action='store_true',
                         help="Copy base files that have no overlay into the out/ directory instead of skipping them")
    parser.add_argument('--videos-only', action='store_true',
                         help="Only process video files, skipping images")
    args = parser.parse_args()

    print("Snapchat Memories Batch Combiner")
    print("="*60)

    # Create output directories
    OUTPUT_DIR.mkdir(exist_ok=True)

    # Scan for memory pairs
    print("Scanning memories directory...")
    pairs = scan_memories()

    if args.videos_only:
        pairs = [pair for pair in pairs if pair.is_video]

    print(f"Found {len(pairs)} memory files\n")

    # Process all pairs
    for i, pair in enumerate(pairs, 1):
        print(f"[{i}/{len(pairs)}] Processing: {pair.base_path.name}")

        # Combine with overlay if present
        if pair.overlay_path:
            combined_name = pair.base_path.name.replace('-main', '-combined')
            combined_path = OUTPUT_DIR / combined_name

            if args.check_previous and combined_path.exists():
                print(f"  → Already processed, skipped")
                print()
                continue

            if pair.is_video:
                combine_video(pair.base_path, pair.overlay_path, combined_path)
            else:
                combine_image(pair.base_path, pair.overlay_path, combined_path)

            print(f"  → Combined: {combined_path.name}")
        elif args.include_bases:
            base_output_path = OUTPUT_DIR / pair.base_path.name

            if args.check_previous and base_output_path.exists():
                print(f"  → Already processed, skipped")
                print()
                continue

            shutil.copy2(pair.base_path, base_output_path)
            print(f"  → Copied: {base_output_path.name}")
        else:
            print("  → No overlay found, skipped")

        print()

    # Summary
    print("="*60)
    print("Processing complete!")
    print(f"\nCombined files: {OUTPUT_DIR}/")


if __name__ == "__main__":
    main()

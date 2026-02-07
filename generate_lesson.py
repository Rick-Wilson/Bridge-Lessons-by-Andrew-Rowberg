#!/usr/bin/env python3
"""Generate tag-driven lesson files for Bridge-Classroom from annotated PBN.

Reads the annotated PBN from the Results folder and generates a South-perspective
lesson file with [BID] tags for interactive student practice.
"""

import re
import os
import subprocess

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
INPUT_FILE = os.path.join(SCRIPT_DIR, 'Results', 'Precision 1C Auctions with Notes.pbn')
OUTPUT_DIR = os.path.join(SCRIPT_DIR, 'Lock-Step Lessons')
OUTPUT_FILE_RESPONDER = os.path.join(OUTPUT_DIR, 'Precision 1C Responder.pbn')
OUTPUT_FILE_OPENER = os.path.join(OUTPUT_DIR, 'Precision 1C Opener.pbn')
OUTPUT_FILE_MIXED = os.path.join(OUTPUT_DIR, 'Precision 1C Mixed.pbn')


ROTATE_MAP = {'N': 'S', 'S': 'N', 'E': 'W', 'W': 'E'}


def rotate_seat(seat):
    """Rotate a seat 180 degrees: N<->S, E<->W."""
    return ROTATE_MAP[seat]


def rotate_deal_tag(deal_value):
    """Rotate deal tag prefix: 'N:...' -> 'S:...' etc."""
    prefix = deal_value[0]
    return rotate_seat(prefix) + deal_value[1:]


def format_bid_display(bid):
    """Convert a bid to display format with suit symbols.

    '2H' -> '2\\H', 'Pass' -> 'pass', 'X' -> 'X', '3NT' -> '3NT'
    """
    bid = bid.rstrip('?')
    if bid.upper() in ('PASS', 'P'):
        return 'pass'
    m = re.match(r'^(\d)(C|D|H|S)$', bid)
    if m:
        return f'{m.group(1)}\\{m.group(2)}'
    return bid  # NT bids, X, XX, etc.


def is_valid_bid_token(token):
    """Check if token is a valid bid (not informal text like 'relay' or 'etc.')."""
    t = token.rstrip('?')
    if t.upper() in ('PASS', 'P', 'X', 'XX'):
        return True
    if re.match(r'^\d(C|D|H|S|NT)$', t):
        return True
    return False


def parse_boards(filepath):
    """Parse PBN file into list of board dicts."""
    with open(filepath) as f:
        content = f.read()

    lines = content.split('\n')
    boards = []
    current = None
    in_auction = False
    in_commentary = False
    commentary_text = ''

    for line in lines:
        stripped = line.strip()

        if stripped.startswith('%'):
            continue

        tag_match = re.match(r'\[(\w+)\s+"(.*)"\]', stripped)
        if tag_match:
            tag, value = tag_match.group(1), tag_match.group(2)

            if tag == 'Event':
                if current:
                    if commentary_text:
                        current['commentary'] = commentary_text.strip()
                        commentary_text = ''
                    boards.append(current)
                current = {
                    'meta_tags': [],
                    'auction_dealer': 'N',
                    'auction_lines': [],
                    'notes': {},
                    'note_lines': [],
                    'commentary': ''
                }
                in_auction = False
                in_commentary = False

            if tag == 'Auction':
                current['auction_dealer'] = value
                in_auction = True
                continue
            elif tag == 'Note':
                in_auction = False
                current['note_lines'].append(stripped)
                nm = re.match(r'(\d+):(.+)', value)
                if nm:
                    current['notes'][int(nm.group(1))] = nm.group(2)
                continue
            else:
                current['meta_tags'].append((tag, value))
                continue

        if stripped.startswith('{'):
            in_auction = False
            in_commentary = True
            commentary_text += stripped + '\n'
            if '}' in stripped[1:]:
                in_commentary = False
            continue

        if in_commentary:
            commentary_text += stripped + '\n'
            if '}' in stripped:
                in_commentary = False
            continue

        if in_auction and stripped:
            current['auction_lines'].append(stripped)
            continue

    if current:
        if commentary_text:
            current['commentary'] = commentary_text.strip()
        boards.append(current)

    return boards


def parse_auction_bids(auction_lines, dealer='N'):
    """Parse auction lines into list of (seat, bid, note_num) tuples.

    Stops at first invalid token (informal text) or 'or' (alternative branch).
    """
    seats = ['N', 'E', 'S', 'W']
    dealer_idx = seats.index(dealer)

    tokens = ' '.join(auction_lines).split()

    bids = []
    bid_idx = 0
    i = 0

    while i < len(tokens):
        token = tokens[i]

        # Stop at 'or' (alternative branch)
        if token.lower() == 'or':
            break

        # Skip stray note annotations
        if re.match(r'^=\d+=$', token):
            i += 1
            continue

        # Stop at informal text
        if not is_valid_bid_token(token):
            break

        # Normalize P to Pass
        clean = token.rstrip('?')
        if clean.upper() == 'P':
            clean = 'Pass'

        # Check for following note annotation
        note_num = None
        if i + 1 < len(tokens) and re.match(r'^=(\d+)=$', tokens[i + 1]):
            note_num = int(tokens[i + 1].strip('='))
            i += 2
        else:
            i += 1

        seat = seats[(dealer_idx + bid_idx) % 4]
        bids.append((seat, clean, note_num))
        bid_idx += 1

    return bids


def trim_trailing_passes(bids):
    """Remove trailing unannotated passes (the standard auction ending)."""
    while bids and bids[-1][1].upper() == 'PASS' and bids[-1][2] is None:
        bids.pop()
    return bids


def format_meaning(meaning):
    """Format a bid meaning with the appropriate verb/phrasing."""
    if not meaning:
        return ''
    if meaning == 'waiting':
        return ' waiting'
    if meaning in ('cue', 'cue bid'):
        return ' showing a control'
    if meaning.startswith('to play'):
        return f' {meaning}'
    if meaning.endswith('?'):
        return f' asking {meaning}'
    if meaning in ('sets trump', 'set trump'):
        return ', setting trump'
    if meaning in ('sets suit', 'set suit'):
        return ', setting the suit'
    if meaning == 'start cuebidding':
        return ', starting cuebidding'
    if meaning == 'forced':
        return ', forced'
    if meaning == 'relay':
        return ', a relay'
    if 'keycard' in meaning and 'keycards' not in meaning:
        return f' asking {meaning}'
    if meaning.startswith('ask'):
        return f', asking{meaning[3:]}'
    return f' showing {meaning}'


def generate_south_commentary(bids, notes, board_num, existing_commentary):
    """Generate structured [show S] commentary for a South lesson."""
    lines = []

    for seat, bid, note_num in bids:
        meaning = notes.get(note_num, '') if note_num else ''
        display = format_bid_display(bid)

        if seat == 'N':
            if bid.upper() == 'PASS':
                line = 'North passes'
            elif bid == 'X':
                line = 'North doubles'
            elif bid == 'XX':
                line = 'North redoubles'
            else:
                line = f'North bids {display}'
            line += format_meaning(meaning)
            line += '.'
            lines.append(line)

        elif seat == 'S':
            lines.append(f'What will you bid now? [BID {display}]')
            if bid.upper() == 'PASS':
                line = 'You pass'
            elif bid == 'X':
                line = 'You double'
            elif bid == 'XX':
                line = 'You redouble'
            else:
                line = f'You bid {display}'
            line += format_meaning(meaning)
            line += '.'
            lines.append(line)
            lines.append('')  # blank line after South's action

        elif seat in ('E', 'W'):
            name = 'East' if seat == 'E' else 'West'
            if bid.upper() != 'PASS':
                if bid == 'X':
                    lines.append(f'{name} doubles.')
                elif bid == 'XX':
                    lines.append(f'{name} redoubles.')
                else:
                    lines.append(f'{name} bids {display}.')

    # Build commentary block
    body = '\n'.join(lines).strip()

    result = f'{{Precision 1C {board_num}}}\n'
    result += '{[show S]\n\n'
    result += body
    result += '\n\n[show NS]\n\n'

    if existing_commentary:
        # Extract text from { } wrappers
        text = existing_commentary
        text = re.sub(r'^\{', '', text)
        text = re.sub(r'\}\s*$', '', text)
        text = text.strip()
        if text:
            result += text + '\n\n'

    result += '}'

    return result


def get_tag(tags, name):
    """Get a tag value from meta_tags list."""
    for tag, value in tags:
        if tag == name:
            return value
    return ''


def write_lesson_pbn(boards, output_path, event_name, skill_path,
                     rotate='none'):
    """Write lesson PBN file.

    rotate: 'none', 'all', or 'alternate' (odd boards rotated).
    """
    with open(output_path, 'w') as f:
        f.write('% PBN 2.1\n')
        f.write('% EXPORT\n')
        f.write('%Content-type: text/x-pbn; charset=ISO-8859-1\n')
        f.write('\n')

        for board_idx, board in enumerate(boards):
            board_num = get_tag(board['meta_tags'], 'Board')

            do_rotate = (rotate == 'all'
                         or (rotate == 'alternate' and board_idx % 2 == 1))

            # Write metadata tags
            for tag, value in board['meta_tags']:
                if tag == 'Event':
                    value = event_name
                elif tag == 'Deal' and do_rotate:
                    value = rotate_deal_tag(value)
                f.write(f'[{tag} "{value}"]\n')

            # Auction dealer (rotated if needed)
            dealer = board['auction_dealer']
            if do_rotate:
                dealer = rotate_seat(dealer)
            f.write(f'[Auction "{dealer}"]\n')
            for line in board['auction_lines']:
                f.write(f'{line}\n')

            # Write notes
            for note_line in board['note_lines']:
                f.write(f'{note_line}\n')

            # Write lesson-specific tags
            f.write('[Student "S"]\n')
            f.write('[BCFlags "1f"]\n')
            f.write('[Category "Precision"]\n')
            f.write('[Difficulty "intermediate"]\n')
            f.write(f'[SkillPath "{skill_path}"]\n')

            # Generate and write commentary
            bids = parse_auction_bids(board['auction_lines'], dealer)
            bids = trim_trailing_passes(bids)

            commentary = generate_south_commentary(
                bids, board['notes'], board_num, board['commentary']
            )
            f.write(commentary + '\n')
            f.write('\n')

    print(f'Wrote {len(boards)} boards to {output_path}')


PBN_TO_PDF = '/Applications/Bridge Utilities/pbn-to-pdf'


def generate_pdf(pbn_path):
    """Generate PDF from a lesson PBN file."""
    pdf_path = pbn_path.replace('.pbn', '.pdf')
    cmd = [PBN_TO_PDF, pbn_path, '-o', pdf_path, '-n', '1', '-v']
    print(f'Running: {" ".join(cmd)}')
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr)
    if result.returncode == 0:
        print(f'Wrote PDF to {pdf_path}')
    else:
        print(f'pbn-to-pdf exited with code {result.returncode}')


def main():
    boards = parse_boards(INPUT_FILE)
    print(f'Parsed {len(boards)} boards from source PBN')

    write_lesson_pbn(boards, OUTPUT_FILE_RESPONDER,
                     event_name='Precision 1C - Responder',
                     skill_path='precision/1c_responder')
    generate_pdf(OUTPUT_FILE_RESPONDER)

    write_lesson_pbn(boards, OUTPUT_FILE_OPENER,
                     event_name='Precision 1C - Opener',
                     skill_path='precision/1c_opener',
                     rotate='all')
    generate_pdf(OUTPUT_FILE_OPENER)

    write_lesson_pbn(boards, OUTPUT_FILE_MIXED,
                     event_name='Precision 1C - Mixed',
                     skill_path='precision/1c_mixed',
                     rotate='alternate')
    generate_pdf(OUTPUT_FILE_MIXED)


if __name__ == '__main__':
    main()

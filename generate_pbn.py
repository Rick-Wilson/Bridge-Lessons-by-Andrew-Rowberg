#!/usr/bin/env python3
"""
Precision 1C Auctions PBN Generator

Stage 1: Parse auction text file into CSV
Stage 2: Expand raw auctions into full auctions (fill passes, remove brackets)
Stage 3: Extract bid notes from auctions, replace with flags
Stage 4: Merge noted CSV with original PBN deal data into output PBN
Stage 5: Generate PDF from output PBN via pbn-to-pdf
"""

import csv
import re
import sys
import os
import argparse
import subprocess


def stage1(txt_path, csv_path):
    """Parse the auction text file and extract auction info into a CSV.

    CSV columns: deal, auction, notes
    - Bid annotations in parens remain inline in the auction column.
    - Deal-level notes (Note:/comment lines) go in the notes column.
    - Alternate auctions (Alternative:/Without interference:/No interference:)
      get their own row with a "-1", "-2" suffix on the deal number.
    """
    with open(txt_path, 'r') as f:
        lines = f.readlines()

    # Collect deal blocks: each starts with "N) " pattern
    deal_blocks = []
    current_block = None
    consecutive_blanks = 0

    for line in lines:
        stripped = line.strip()

        if not stripped:
            consecutive_blanks += 1
            # Two+ blank lines in a row after last deal = end of auction section
            if consecutive_blanks >= 2 and current_block:
                deal_blocks.append(current_block)
                current_block = None
            continue
        consecutive_blanks = 0

        if current_block is None and deal_blocks:
            # We've already ended; ignore remaining lines
            break

        # New deal line?
        deal_match = re.match(r'^(\d+)\)\s+(.*)', stripped)
        if deal_match:
            deal_num = int(deal_match.group(1))
            if deal_num > 40:
                break
            if current_block:
                deal_blocks.append(current_block)
            current_block = {
                'num': deal_num,
                'auction': deal_match.group(2).strip(),
                'extra_lines': []
            }
        elif current_block:
            current_block['extra_lines'].append(stripped)

    # Don't forget the last block
    if current_block and current_block['num'] <= 40:
        deal_blocks.append(current_block)

    # Process each block into CSV rows
    rows = []
    for block in deal_blocks:
        deal_num = str(block['num'])
        auction = block['auction']
        notes = []
        alternates = []

        for extra in block['extra_lines']:
            # Alternate auction: label + colon + auction starting with "1C"
            alt_match = re.match(
                r'^(Alternative|Without interference|No interference):\s*(1C\b.*)',
                extra, re.IGNORECASE
            )
            if alt_match:
                alternates.append((alt_match.group(1).strip(),
                                   alt_match.group(2).strip()))
                continue

            # Explicit note line
            note_match = re.match(r'^\*?Note:\s*(.*)', extra)
            if note_match:
                notes.append(note_match.group(1).strip())
                continue

            # Anything else is a continuation comment/note
            notes.append(extra)

        notes_text = ' '.join(notes).strip()
        rows.append((deal_num, auction, notes_text))

        for i, (label, alt_auction) in enumerate(alternates, 1):
            rows.append((f"{deal_num}-{i}", alt_auction, label))

    # Write CSV
    with open(csv_path, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['deal', 'auction', 'notes'])
        for row in rows:
            writer.writerow(row)

    print(f"Stage 1 complete: wrote {len(rows)} rows to {csv_path}")


def expand_auction(raw_auction):
    """Expand a raw auction string into a full auction with all passes.

    Split on ' - ' (or ' – '), iterate elements:
    - Bracketed elements like [2C]: remove brackets, output as-is
    - Non-bracketed elements: if previous was also non-bracketed, insert Pass first
    - 'all pass': output Pass Pass Pass
    Ensure auction ends with three consecutive Passes.
    Parens (bid annotations) are preserved.
    """
    # Normalize dash variants to plain hyphen
    normalized = raw_auction.replace(' – ', ' - ')

    # Split on ' - ' but NOT inside parentheses
    elements = []
    current = ''
    paren_depth = 0
    i = 0
    while i < len(normalized):
        ch = normalized[i]
        if ch == '(':
            paren_depth += 1
            current += ch
        elif ch == ')':
            paren_depth -= 1
            current += ch
        elif paren_depth == 0 and normalized[i:i+3] == ' - ':
            elements.append(current)
            current = ''
            i += 3
            continue
        else:
            current += ch
        i += 1
    if current:
        elements.append(current)

    output = []
    prev_was_bracketed = False

    for i, elem in enumerate(elements):
        elem = elem.strip()
        if not elem:
            continue

        # Handle "all pass"
        if elem.lower() == 'all pass':
            output.extend(['Pass', 'Pass', 'Pass'])
            prev_was_bracketed = False
            continue

        # Check if bracketed (opponent bid)
        if elem.startswith('[') and ']' in elem:
            # Remove brackets: [2C] -> 2C, [P] -> P
            bid = elem.replace('[', '').replace(']', '')
            output.append(bid)
            prev_was_bracketed = True
        else:
            # N/S bid - insert Pass if previous was also N/S
            if i > 0 and not prev_was_bracketed:
                output.append('Pass')
            output.append(elem)
            prev_was_bracketed = False

    # Normalize P to Pass
    for i, elem in enumerate(output):
        if elem == 'P':
            output[i] = 'Pass'
        elif elem.startswith('P ') or elem.startswith('P('):
            output[i] = 'Pass' + elem[1:]

    # Ensure auction ends with three Passes (ignore annotations)
    def is_pass(bid):
        return bid.split('(')[0].strip() == 'Pass'

    while len(output) < 3 or not all(is_pass(x) for x in output[-3:]):
        output.append('Pass')

    return ' '.join(output)


def stage2(csv_in_path, csv_out_path):
    """Read stage 1 CSV, expand auctions, write stage 2 CSV."""
    with open(csv_in_path, 'r') as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    # Build lookup of raw auctions by deal number (for "Like #N" refs)
    raw_by_deal = {}
    for row in rows:
        raw_by_deal[row['deal']] = row['auction']

    output_rows = []
    for row in rows:
        raw = row['auction']

        # Handle "Like #N" references
        if raw.startswith('Like #'):
            ref_num = raw.split('#')[1].strip()
            raw = raw_by_deal.get(ref_num, raw)

        expanded = expand_auction(raw)
        output_rows.append((row['deal'], expanded, row['notes']))

    with open(csv_out_path, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['deal', 'auction', 'notes'])
        for r in output_rows:
            writer.writerow(r)

    print(f"Stage 2 complete: wrote {len(output_rows)} rows to {csv_out_path}")


def extract_bid_notes(auction):
    """Extract parenthetical bid notes from an auction string.

    Walk the string, find each (...) group, replace with =N= flag.
    Returns (cleaned_auction, bid_notes_str) where bid_notes_str is
    like "1:1-4-4-4, 8+ HCP|2:waiting|..."
    """
    result = ''
    notes = []
    note_num = 0
    i = 0
    while i < len(auction):
        if auction[i] == '(':
            # Find matching close paren
            depth = 1
            j = i + 1
            while j < len(auction) and depth > 0:
                if auction[j] == '(':
                    depth += 1
                elif auction[j] == ')':
                    depth -= 1
                j += 1
            # Extract note text (without parens)
            note_text = auction[i+1:j-1]
            note_num += 1
            notes.append(f"{note_num}:{note_text}")
            # Replace with flag; strip leading space before paren
            result = result.rstrip() + f' ={note_num}='
            i = j
        else:
            result += auction[i]
            i += 1

    # Clean up any double spaces
    while '  ' in result:
        result = result.replace('  ', ' ')

    bid_notes_str = '|'.join(notes)
    return result.strip(), bid_notes_str


def add_suit_symbols(text):
    """Add PBN suit symbol escapes to suit abbreviations in note text."""
    # Count + space + suit letter: "5+ C" -> "5+\Cs", "4 S" -> "4\Ss"
    text = re.sub(r'(\d[\d+-]*) ([CDHS])\b', r'\1\\\2s', text)
    # Card rank + suit letter: "QH" -> "Q\H", "KS" -> "K\S"
    text = re.sub(r'([KQAJT])([CDHS])\b', r'\1\\\2', text)
    # Bid reference (digit + suit, no space): "1S" -> "1\S", "3D" -> "3\D"
    text = re.sub(r'(\d)([CDHS])\b', r'\1\\\2', text)
    # Suit before "keycard": "H keycard" -> "\H keycard"
    text = re.sub(r'\b([CDHS]) (keycard)', r'\\\1 \2', text)
    # "for" + suit: "keycard for C" -> "keycard for \C"
    text = re.sub(r'(for )([CDHS])\b', r'\1\\\2', text)
    return text


def stage3(csv_in_path, csv_out_path):
    """Read stage 2 CSV, extract bid notes into flags, write stage 3 CSV."""
    with open(csv_in_path, 'r') as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    output_rows = []
    for row in rows:
        cleaned_auction, bid_notes = extract_bid_notes(row['auction'])
        bid_notes = add_suit_symbols(bid_notes)
        notes = add_suit_symbols(row['notes'])
        output_rows.append((row['deal'], cleaned_auction, bid_notes,
                            notes))

    with open(csv_out_path, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['deal', 'auction', 'bid_notes', 'notes'])
        for r in output_rows:
            writer.writerow(r)

    print(f"Stage 3 complete: wrote {len(output_rows)} rows to {csv_out_path}")


def parse_pbn_boards(pbn_path):
    """Parse PBN file, return dict of board_num_str -> dict of tag values."""
    boards = {}
    current_tags = {}

    with open(pbn_path, 'r') as f:
        for line in f:
            line = line.strip()
            if not line:
                if current_tags and 'Board' in current_tags:
                    boards[current_tags['Board']] = current_tags
                    current_tags = {}
                continue
            if line.startswith('[') and '"' in line:
                tag_end = line.index(' ')
                tag_name = line[1:tag_end]
                tag_value = line[line.index('"')+1:line.rindex('"')]
                current_tags[tag_name] = tag_value

    # Last block
    if current_tags and 'Board' in current_tags:
        boards[current_tags['Board']] = current_tags

    return boards


def format_auction_lines(auction_str):
    """Format auction string into lines of 4 bids each.

    A 'bid' is a token optionally followed by an =N= flag.
    Output lines of 4 bids for readability (one round per line).
    """
    tokens = auction_str.split()

    # Group tokens into bids (bid + optional =N= flag)
    bids = []
    i = 0
    while i < len(tokens):
        bid = tokens[i]
        if (i + 1 < len(tokens)
                and tokens[i+1].startswith('=')
                and tokens[i+1].endswith('=')):
            bid += ' ' + tokens[i+1]
            i += 2
        else:
            i += 1
        bids.append(bid)

    # 4 bids per line
    lines = []
    for j in range(0, len(bids), 4):
        lines.append(' '.join(bids[j:j+4]))

    return '\n'.join(lines)


def stage4(csv_in_path, pbn_in_path, pbn_out_path):
    """Merge stage 3 CSV with original PBN deal data into output PBN."""
    boards = parse_pbn_boards(pbn_in_path)

    with open(csv_in_path, 'r') as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    with open(pbn_out_path, 'w') as f:
        f.write('% PBN 2.1\n')
        f.write('% EXPORT\n')
        f.write('%Content-type: text/x-pbn; charset=ISO-8859-1\n\n')

        for row in rows:
            deal_id = row['deal']
            # Base board number (strip alternate suffix)
            base_num = deal_id.split('-')[0]

            board = boards.get(base_num)
            if not board:
                print(f"Warning: no PBN data for board {base_num}")
                continue

            # Write tags
            f.write(f'[Event "{board.get("Event", "")}"]\n')
            f.write(f'[Site "{board.get("Site", "")}"]\n')
            f.write(f'[Date "{board.get("Date", "")}"]\n')
            f.write(f'[Board "{deal_id}"]\n')
            f.write('[West ""]\n')
            f.write('[North ""]\n')
            f.write('[East ""]\n')
            f.write('[South ""]\n')
            f.write(f'[Dealer "{board.get("Dealer", "N")}"]\n')
            f.write(f'[Vulnerable "{board.get("Vulnerable", "None")}"]\n')
            f.write(f'[Deal "{board.get("Deal", "")}"]\n')
            f.write('[Scoring ""]\n')
            f.write('[Declarer ""]\n')
            f.write('[Contract ""]\n')
            f.write('[Result ""]\n')

            # Auction
            dealer = board.get('Dealer', 'N')
            f.write(f'[Auction "{dealer}"]\n')
            f.write(format_auction_lines(row['auction']) + '\n')

            # Bid notes
            if row['bid_notes']:
                for entry in row['bid_notes'].split('|'):
                    f.write(f'[Note "{entry}"]\n')

            # Deal-level notes as PBN comment
            if row['notes']:
                f.write(f'{{ {row["notes"]} }}\n')

            f.write('\n')

    print(f"Stage 4 complete: wrote {len(rows)} boards to {pbn_out_path}")


PBN_TO_PDF = '/Applications/Bridge Utilities/pbn-to-pdf'


def stage5(pbn_path, pdf_path):
    """Generate PDF from PBN file using pbn-to-pdf."""
    cmd = [PBN_TO_PDF, pbn_path, '-o', pdf_path, '-n', '1', '-v']
    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr)
    if result.returncode != 0:
        print(f"pbn-to-pdf exited with code {result.returncode}")
        sys.exit(1)
    print(f"Stage 5 complete: wrote {pdf_path}")


def main():
    parser = argparse.ArgumentParser(
        description='Precision 1C Auctions PBN Generator')
    parser.add_argument('--stage', type=int, required=True,
                        choices=[1, 2, 3, 4, 5],
                        help='1=parse, 2=expand, 3=notes, 4=merge PBN, '
                             '5=generate PDF')
    args = parser.parse_args()

    base_dir = os.path.dirname(os.path.abspath(__file__))
    txt_path = os.path.join(base_dir, 'Original Material',
                            'Precision 1C Auctions and System.txt')
    pbn_in_path = os.path.join(base_dir, 'Original Material',
                               'Precision 1C Leveled by Responses x100.pbn')
    csv_path = os.path.join(base_dir, 'Intermediate Results',
                            'temp_auctions.csv')
    csv2_path = os.path.join(base_dir, 'Intermediate Results',
                             'temp_auctions_expanded.csv')
    csv3_path = os.path.join(base_dir, 'Intermediate Results',
                             'temp_auctions_noted.csv')
    pbn_out_path = os.path.join(base_dir, 'Results',
                                'Precision 1C Auctions with Notes.pbn')
    pdf_out_path = os.path.join(base_dir, 'Results',
                                'Precision 1C Auctions with Notes.pdf')

    if args.stage == 1:
        stage1(txt_path, csv_path)
    elif args.stage == 2:
        stage2(csv_path, csv2_path)
    elif args.stage == 3:
        stage3(csv2_path, csv3_path)
    elif args.stage == 4:
        stage4(csv3_path, pbn_in_path, pbn_out_path)
    elif args.stage == 5:
        stage5(pbn_out_path, pdf_out_path)


if __name__ == '__main__':
    main()

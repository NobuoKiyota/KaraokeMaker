import sys
import os
import re
import pypdf
import openpyxl

def extract_score_info(pdf_path):
    """PDFからテキストを抽出し、小節・セクション・コードを解析する"""
    if not os.path.exists(pdf_path):
        raise FileNotFoundError(f"PDF file not found: {pdf_path}")
        
    reader = pypdf.PdfReader(pdf_path)
    
    # 認識対象のセクション名リスト
    section_keywords = [
        "Intro1", "Intro2", "1A", "1B", "1Sabi", 
        "2A", "2B", "2Sabi", "1G.Solo", "2G.Solo", 
        "1C", "3Sabi", "4Sabi", "Outro1", "Outro2", "Ending"
    ]
    
    # コードネーム判定の正規表現
    # 例: Gm7, Cm9, D7(9), Emaj7, B, F, A7, D7(b9), Gm7(add9), E9, F7 などにマッチ
    chord_regex = re.compile(
        r'([A-G][b#]?(?:maj|min|m|dim|aug|sus)?(?:\d+|add\d+)?(?:\([b#]?\d+\))?)',
        re.IGNORECASE
    )

    all_pages_text = []
    for page in reader.pages:
        all_pages_text.append(page.extract_text())
        
    full_text = "\n".join(all_pages_text)
    lines = [line.strip() for line in full_text.split("\n") if line.strip()]
    
    parsed_events = []
    
    current_section = "Intro1"
    current_measure = 1
    
    i = 0
    while i < len(lines):
        line = lines[i]
        
        # 1. セクション名の検出
        sec_found = False
        for sec in section_keywords:
            if sec in line:
                current_section = sec
                sec_found = True
                break
        if sec_found:
            i += 1
            continue
            
        # 2. 小節番号の検出
        # "V." または "Voice" の直後の行、あるいは単独の数字行を小節番号とみなす
        if line == "V." or line == "Voice":
            if i + 1 < len(lines):
                next_line = lines[i+1]
                if next_line.isdigit():
                    current_measure = int(next_line)
                    i += 2
                    continue
        elif line.isdigit():
            val = int(line)
            # ページ番号や一時的な極端に大きい数値以外を小節番号として同期
            if val < 300 and val > current_measure:
                current_measure = val
                i += 1
                continue

        # 3. コード進行の検出
        chords_in_line = []
        tokens = line.split()
        for token in tokens:
            # 特殊記号やページ表示などの不要なトークンを除外
            if any(char in token for char in ['', '', '', '', '', '']):
                continue
            matches = chord_regex.findall(token)
            if matches:
                chords_in_line.extend(matches)
                
        if chords_in_line:
            m = current_measure
            for chord in chords_in_line:
                parsed_events.append({
                    "measure": m,
                    "beat": 1.0,
                    "section": current_section,
                    "chord": chord,
                    "lyric": "none",
                    "line_num": 1
                })
                # コードごとに1小節進める
                m += 1
            # 同期小節番号を更新
            current_measure = m
            
        i += 1
        
    # 重複と不要な連続を整理
    unique_events = []
    seen = set()
    for ev in parsed_events:
        # 同一小節・同一拍での重複を防止
        key = (ev["measure"], ev["beat"])
        if key not in seen:
            seen.add(key)
            unique_events.append(ev)
            
    # 小節・拍でソート
    unique_events.sort(key=lambda x: (x["measure"], x["beat"]))
    return unique_events

def create_excel_from_events(events, template_path, output_path):
    """パースされたイベントをタイミングExcelテンプレートへ流し込む"""
    if not os.path.exists(template_path):
        raise FileNotFoundError(f"Template Excel file not found: {template_path}")
        
    wb = openpyxl.load_workbook(template_path)
    sheet = wb.active
    
    # テンプレートのメタデータを初期設定（PDF内の情報から適宜推定）
    sheet["B2"] = 172                 # テンポ (ツギハギのヒロインの標準テンポ: 172)
    sheet["B3"] = "G minor"           # Key
    sheet["B4"] = "ツギハギのヒロイン"  # タイトル
    sheet["B5"] = "test.wav"          # デフォルト音声ファイル名
    
    # 9行目以降の既存行をクリア
    max_r = sheet.max_row
    if max_r >= 9:
        sheet.delete_rows(9, amount=max_r - 8)
        
    # 新しいデータを書き込み
    for r_idx, ev in enumerate(events):
        row_num = 9 + r_idx
        sheet.cell(row=row_num, column=1, value=ev["measure"])
        sheet.cell(row=row_num, column=2, value=ev["beat"])
        sheet.cell(row=row_num, column=3, value=ev["section"])
        sheet.cell(row=row_num, column=4, value=ev["chord"])
        sheet.cell(row=row_num, column=5, value=ev["lyric"])
        sheet.cell(row=row_num, column=6, value=ev["line_num"])
        
    # 終端記号の追加
    end_row = 9 + len(events)
    sheet.cell(row=end_row, column=1, value="End")
    
    wb.save(output_path)
    print(f"Successfully generated: {output_path} ({len(events)} rows)")

def main():
    default_pdf = r"Z:\KaraokeMaker\Resources\ツギハギのヒロインScore.pdf"
    default_template = r"Z:\KaraokeMaker\Resources\karaokemaker_template.xlsx"
    default_output = r"Z:\KaraokeMaker\Resources\ツギハギのヒロイン_parsed.xlsx"
    
    pdf_path = sys.argv[1] if len(sys.argv) > 1 else default_pdf
    output_path = sys.argv[2] if len(sys.argv) > 2 else default_output
    
    print(f"--- Score PDF Converter ---")
    print(f"Input PDF: {pdf_path}")
    print(f"Template:  {default_template}")
    
    try:
        events = extract_score_info(pdf_path)
        print(f"Extracted {len(events)} unique chord events from PDF.")
        create_excel_from_events(events, default_template, output_path)
        print(f"Conversion completed successfully.")
    except Exception as e:
        print(f"Error during conversion: {e}")

if __name__ == "__main__":
    main()

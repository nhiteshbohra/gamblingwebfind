import os
import argparse
from pypdf import PdfWriter

def merge_pdfs(input_files, output_file):
    """
    Merges a list of PDF files in the given sequence into a single output PDF.
    
    :param input_files: List of file paths to PDF files in order of merging.
    :param output_file: Path to the output merged PDF file.
    """
    writer = PdfWriter()

    for file in input_files:
        if os.path.exists(file):
            print(f"Adding: {file}")
            writer.append(file)
        else:
            print(f"Warning: File '{file}' not found. Skipping...")

    print(f"Saving merged PDF to '{output_file}'...")
    with open(output_file, "wb") as out_f:
        writer.write(out_f)
    
    writer.close()
    print("PDF merge completed successfully!")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Merge selected PDF files in a specific sequence.")
    parser.add_argument("-o", "--output", default="merged_output.pdf", help="Output merged PDF filename (default: merged_output.pdf)")
    parser.add_argument("inputs", nargs="+", help="Input PDF files in the order they should be merged")
    
    args = parser.parse_args()
    merge_pdfs(args.inputs, args.output)

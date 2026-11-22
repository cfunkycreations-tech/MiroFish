"""
Text processing service
"""

from typing import List, Optional
from ..utils.file_parser import FileParser, split_text_into_chunks


class TextProcessor:
    """The text processor"""
    
    @staticmethod
    def extract_from_files(file_paths: List[str]) -> str:
        """Extract the text from several files"""
        return FileParser.extract_from_multiple(file_paths)
    
    @staticmethod
    def split_text(
        text: str,
        chunk_size: int = 500,
        overlap: int = 50
    ) -> List[str]:
        """
        Split the text
        
        Args:
            text: the original text
            chunk_size: the chunk size
            overlap: the overlap size
            
        Returns:
            the list of text chunks
        """
        return split_text_into_chunks(text, chunk_size, overlap)
    
    @staticmethod
    def preprocess_text(text: str) -> str:
        """
        Preprocess the text
        - remove the extra whitespace
        - normalize the line endings
        
        Args:
            text: the original text
            
        Returns:
            the processed text
        """
        import re
        
        # Normalize the line endings
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        
        # Remove repeated blank lines (keep at most two newlines)
        text = re.sub(r'\n{3,}', '\n\n', text)
        
        # Trim the whitespace at the start and end of each line
        lines = [line.strip() for line in text.split('\n')]
        text = '\n'.join(lines)
        
        return text.strip()
    
    @staticmethod
    def get_text_stats(text: str) -> dict:
        """Fetch the text statistics"""
        return {
            "total_chars": len(text),
            "total_lines": text.count('\n') + 1,
            "total_words": len(text.split()),
        }


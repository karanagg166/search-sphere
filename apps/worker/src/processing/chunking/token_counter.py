import tiktoken


class TokenCounter:
    """
    Token-counting and token-bounded text utility using tiktoken.

    Encapsulates tokenizer interactions so tokenizer calls are not spread across
    the chunking and processing layers.
    """

    def __init__(self, encoding_name: str = "cl100k_base") -> None:
        self.encoding_name = encoding_name
        self._encoding: tiktoken.Encoding | None = None

    @property
    def encoding(self) -> tiktoken.Encoding:
        """Lazily load and cache the tiktoken encoding."""
        if self._encoding is None:
            self._encoding = tiktoken.get_encoding(self.encoding_name)
        return self._encoding

    def count(self, text: str) -> int:
        """
        Count the number of tokens in the given text.
        Empty or whitespace-only text returns 0.
        """
        if not text or not text.strip():
            return 0
        return len(self.encoding.encode(text))

    def truncate(self, text: str, max_tokens: int) -> str:
        """
        Truncate text to at most max_tokens tokens.
        Preserves complete text if count <= max_tokens.
        """
        if max_tokens <= 0:
            return ""
        if self.count(text) <= max_tokens:
            return text

        tokens = self.encoding.encode(text)
        truncated_tokens = tokens[:max_tokens]
        return self.encoding.decode(truncated_tokens).strip()

    def split_by_tokens(self, text: str, max_tokens: int) -> list[str]:
        """
        Hard fallback: split text into segments each having at most max_tokens tokens.
        Prefers splitting along word boundaries to avoid cutting words in half.
        If a single word itself exceeds max_tokens, splits token-by-token.
        """
        if not text or not text.strip():
            return []

        if self.count(text) <= max_tokens:
            return [text.strip()]

        words = text.split()
        segments: list[str] = []
        current_words: list[str] = []

        for word in words:
            # Check candidate if we append this word
            candidate = " ".join(current_words + [word]) if current_words else word
            if self.count(candidate) <= max_tokens:
                current_words.append(word)
            else:
                if current_words:
                    segments.append(" ".join(current_words))
                    current_words = []

                # If the single word itself exceeds max_tokens, split it token-by-token
                if self.count(word) > max_tokens:
                    word_tokens = self.encoding.encode(word)
                    for i in range(0, len(word_tokens), max_tokens):
                        chunk_str = self.encoding.decode(
                            word_tokens[i : i + max_tokens]
                        ).strip()
                        if chunk_str:
                            segments.append(chunk_str)
                else:
                    current_words.append(word)

        if current_words:
            segments.append(" ".join(current_words))

        return [seg for seg in segments if seg.strip()]

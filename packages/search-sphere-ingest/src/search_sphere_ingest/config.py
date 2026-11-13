from pydantic import BaseModel, ConfigDict, Field, model_validator


class PipelineConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    max_file_bytes: int = Field(default=20 * 1024 * 1024, ge=1024, le=50 * 1024 * 1024)
    max_pages: int = Field(default=50, ge=1, le=100)
    max_objects: int = Field(default=20_000, ge=1, le=50_000)
    max_page_points: int = Field(default=14_400, ge=72, le=14_400)
    max_image_pixels: int = Field(default=16_000_000, ge=1, le=32_000_000)
    max_content_bytes: int = Field(default=8 * 1024 * 1024, ge=1024, le=32 * 1024 * 1024)
    max_crop_bytes: int = Field(default=8 * 1024 * 1024, ge=1024, le=16 * 1024 * 1024)
    max_visual_regions: int = Field(default=32, ge=0, le=64)
    max_render_side: int = Field(default=1400, ge=100, le=2000)
    timeout_seconds: float = Field(default=90, gt=0, le=300)
    target_tokens: int = Field(default=400, ge=16, le=8192)
    max_tokens: int = Field(default=480, ge=16, le=8192)
    overlap_sentences: int = Field(default=1, ge=0, le=2)
    continuation_threshold: float = Field(default=0.9, ge=0.8, le=1)
    require_complete: bool = False
    use_opencv: bool = False

    @model_validator(mode="after")
    def budgets(self) -> "PipelineConfig":
        if self.target_tokens > self.max_tokens:
            raise ValueError("target_tokens must not exceed max_tokens")
        return self

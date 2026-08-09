from collections import defaultdict
from dataclasses import dataclass

from app.agents.schemas import ReviewComment, ReviewResult
from app.github.diff_parser import ChangedFile

MAX_COMMENTS_PER_REVIEW = 5


@dataclass
class SuppressedComment:
    comment: ReviewComment
    reason: str
    detail: str | None = None  # critic rationale; `reason` stays a contract string


@dataclass(frozen=True)
class ValidationResult:
    accepted_comments: list[ReviewComment]
    suppressed_comments: list[SuppressedComment]

    @property
    def should_post_review(self) -> bool:
        return bool(self.accepted_comments)


def validate_review_comments(
    *,
    result: ReviewResult,
    files: list[ChangedFile],
) -> ValidationResult:
    legal_lines_by_file = {
        changed_file.path: changed_file.right_side_lines for changed_file in files
    }

    accepted: list[ReviewComment] = []
    suppressed: list[SuppressedComment] = []
    seen_locations: set[tuple[str, int]] = set()
    comments_per_file: defaultdict[str, int] = defaultdict(int)

    for comment in result.comments:
        valid_lines = legal_lines_by_file.get(comment.file_path)

        if valid_lines is None:
            suppressed.append(
                SuppressedComment(
                    comment=comment,
                    reason="file_not_present_in_diff",
                )
            )
            continue

        if comment.line not in valid_lines:
            suppressed.append(
                SuppressedComment(
                    comment=comment,
                    reason="line_not_in_diff",
                )
            )
            continue

        location = (comment.file_path, comment.line)
        if location in seen_locations:
            suppressed.append(
                SuppressedComment(
                    comment=comment,
                    reason="duplicate_comment_location",
                )
            )
            continue

        if len(accepted) >= MAX_COMMENTS_PER_REVIEW:
            suppressed.append(
                SuppressedComment(
                    comment=comment,
                    reason="review_comment_limit_exceeded",
                )
            )
            continue

        if comments_per_file[comment.file_path] >= 3:
            suppressed.append(
                SuppressedComment(
                    comment=comment,
                    reason="per_file_comment_limit_exceeded",
                )
            )
            continue

        accepted.append(comment)
        seen_locations.add(location)
        comments_per_file[comment.file_path] += 1

    return ValidationResult(
        accepted_comments=accepted,
        suppressed_comments=suppressed,
    )

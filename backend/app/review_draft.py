"""Import, review and publish drafts using trusted local database access only."""
import argparse
import sys
from pathlib import Path
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import get_engine
from app.draft_reviews import DraftReviewError, load_draft, publish_draft, review_draft, store_draft


def main() -> int:
    parser = argparse.ArgumentParser(description="Store, review or explicitly publish an AI draft locally. Never calls a model.")
    commands = parser.add_subparsers(dest="command", required=True)
    importer = commands.add_parser("import", help="Validate and store a saved JSON draft")
    importer.add_argument("--path", type=Path, required=True)
    reviewer = commands.add_parser("review", help="Record a final local review decision")
    reviewer.add_argument("--draft-id", type=UUID, required=True)
    reviewer.add_argument("--decision", choices=("approved", "rejected"), required=True)
    reviewer.add_argument("--reviewer", required=True, help="Self-reported local reviewer; not an authenticated identity")
    reviewer.add_argument("--note", required=True, help="Reason for the decision; do not include secrets")
    publisher = commands.add_parser("publish", help="Make an approved, current draft visible through the public read API")
    publisher.add_argument("--draft-id", type=UUID, required=True)
    args = parser.parse_args()
    try:
        # File validation happens before acquiring a database connection.
        document = load_draft(args.path) if args.command == "import" else None
        with Session(get_engine()) as session, session.begin():
            if args.command == "import":
                draft_id = store_draft(session, document)
            else:
                draft_id = args.draft_id
                if args.command == "review":
                    review_draft(session, draft_id, args.decision, args.reviewer, args.note)
                else:
                    publish_draft(session, draft_id)
    except DraftReviewError as error:
        print(str(error), file=sys.stderr)
        return 1
    except (ValidationError, ValueError):
        print("Invalid draft or review data. No changes were saved.", file=sys.stderr)
        return 1
    except (SQLAlchemyError, OSError):
        print("Could not save the draft, decision, or publication. Check the file, database, and migrations.", file=sys.stderr)
        return 1
    if args.command == "import":
        print(f"Stored or found private draft {draft_id}. Reimporting preserves its review status.")
    elif args.command == "review":
        print(f"Recorded {args.decision} for private draft {draft_id}.")
    else:
        print(f"Published approved draft {draft_id} to its run's public investigation.")
    if args.command != "publish":
        print("The public report was not changed.")
    print("No model was called.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

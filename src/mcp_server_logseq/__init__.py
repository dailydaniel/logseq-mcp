from .server import serve


def main():
    """MCP LogSeq Server - AI-powered note taking for MCP"""
    import argparse
    import asyncio
    import os
    from dotenv import load_dotenv

    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Share your LogSeq notes with LLM (https://docs.logseq.com/#/page/local%20http%20server)"
    )
    parser.add_argument(
        "--api-key",
        type=str,
        help="LogSeq API key",
    )
    parser.add_argument(
        "--url",
        type=str,
        help="LogSeq API host",
    )

    args = parser.parse_args()

    # Check for API key in args first, then environment
    api_key = args.api_key or os.getenv("LOGSEQ_API_TOKEN")
    if not api_key:
        parser.error("LogSeq API key must be provided either via --api-key or LOGSEQ_API_TOKEN environment variable")

    # Check for URL in args first, then environment
    url = args.url or os.getenv("LOGSEQ_API_URL")
    if not url:
        parser.error("LogSeq API URL must be provided either via --url or LOGSEQ_API_URL environment variable")

    asyncio.run(serve(api_key, url))


if __name__ == "__main__":
    main()

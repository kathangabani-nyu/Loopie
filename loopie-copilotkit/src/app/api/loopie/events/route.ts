import { NextResponse } from "next/server";

export async function GET() {
  return NextResponse.json({
    message: "Loopie events stream is sourced from Redis via the control agent state.",
    hint: "Use cockpit buttons or loopie_control tools; events populate in agent state.",
  });
}

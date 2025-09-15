import express from "express"
import type { Request, Response } from "express";


const app = express();
const PORT = 9000;

app.all("/", handleRoot)
app.post("/events", handleEvents)

async function handleRoot(req: Request, res: Response) {
    res.status(200).send("<h1>Ready</h1>").end()
}

async function handleEvents(req: Request, res: Response) {
    type Params = {
        Main_sheet: string;
        Contrib_sheet: string;
        attendance_sheet: string;
    }

    
}


app.listen(PORT, () => {
    console.log(`The server is running at : \x1b[34mhttp://localhost:${PORT}\x1b[0m`);
    
})
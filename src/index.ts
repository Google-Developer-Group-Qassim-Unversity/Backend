import express from "express"
import type { Request, Response } from "express";


const app = express();
const PORT = 9000;


app.post("/events", handleEvents)

async function handleEvents(req: Request, res: Response) {
    
}


app.listen(PORT, () => {
    console.log(`The server is running at : \x1b[34mhttp://localhost:${PORT}\x1b[0m`);
    
})
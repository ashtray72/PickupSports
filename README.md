# PickupSports

Ashton Reece, Pranav Balaji, Soham Pandey, Donovan Baran

PickupSports is an ECE 49595 Senior Design project for Purdue students to discover and organize pickup basketball games. The planned iOS app also uses recorded half-court games to produce player statistics, shot charts, and highlights.

The current frontend is a React Native/Expo demo of nearby basketball games. It displays each game's court, distance, time, skill level, and available spots. It uses local mock data and does not require a backend.

## Run the frontend

Use Node.js 24.3 or newer within the Node 24 release line, with npm. Check `node --version` before installing. The app uses Expo SDK 58, React Native 0.88.1, and React 19.3.0. From the repository root:

```sh
cd client
npm ci
npm start
```

If your Mac has both an older `/usr/local/bin/node` and Homebrew Node 24 at `/opt/homebrew/bin/node`, select Homebrew for the current terminal session only:

```sh
export PATH="/opt/homebrew/bin:$PATH"
node --version
npm --version
```

This does not change shell startup files or the system-wide Node configuration. The project lockfile records the installed dependency versions; use `npm ci` to reproduce them.

Open the project in a compatible version of Expo Go on an iPhone connected to the same Wi-Fi network as the development computer. Expo Go must support the Expo SDK used by this project. The App Store only offers the latest Expo Go, which supports one SDK at a time; if Expo Go says the project is incompatible, the project's SDK needs upgrading (`npx expo install expo@^<sdk>.0.0`, then `npx expo install --fix`).

Expo Go requires you to be signed in to the same Expo account on the computer and on the phone. If it says "You need to be signed in to Expo Go and Expo CLI to open your project", create a free account at https://expo.dev/signup (one per person), then sign in on the computer from `client`:

```sh
npx expo login
npx expo whoami   # prints your Expo username when signed in
```

In the Expo Go app, sign in with the same account on its Profile screen, then run `npm start` again and scan the QR code. If the phone can't reach the computer (common on campus Wi-Fi such as eduroam), start with `npx expo start --tunnel` instead. The login is stored on your machine, not in the repository.

For the iOS Simulator on macOS, install and configure Xcode and its iOS Simulator, then run this command from `client`:

```sh
npm run ios
```

If no simulator is available, install an iOS runtime in Xcode's Settings > Components. Keep the Expo terminal running while using the app. To select a simulator, run `npm start` and press Shift+I.

For visual verification, scroll through all four cards and compare their details:

| Court                          | Distance | Time              | Skill    | Available spots |
| ------------------------------ | -------- | ----------------- | -------- | --------------- |
| Purdue CoRec - Court 2         | 0.04 mi  | 11:30 AM–12:30 PM | Mid      | 3 spots left    |
| Purdue CoRec - Court 2         | 0.04 mi  | 1:00 PM–2:00 PM   | Mid      | Full            |
| Hillenbrand Hall Courts        | 0.16 mi  | 5:00 PM–6:00 PM   | Advanced | 1 spot left     |
| Pickett Park Basketball Courts | 0.30 mi  | 4:30 PM–5:45 PM   | Beginner | 5 spots left    |

Check that the demo label is visible, the two CoRec games remain separate, and longer court names wrap without clipping. Repeat with larger device text to check readability.

## Check the frontend

Run these commands from `client`:

```sh
npm test -- --runInBand
npm run typecheck
npm run lint
npm run format:check
```

The component tests check that game details stay paired with the correct court and time, and that full games and singular/plural spot counts display correctly.

## Demo data

The mock courts and games are based on the prototype data in commit `12d3940acceb11678030bdcbab8b70b485bcfca8`. Distances use a fixed Purdue campus demo location at latitude `40.4282`, longitude `-86.9222`; they do not use the device's location. Game times have no associated date, and the listings and spot counts are never live.

The demo includes four games at three courts, including two separate games at CoRec Court 2. It is a frontend display prototype; game discovery, authentication, joining, and video analysis still need backend integration.

## Project documentation

Semester 1 documentation is in `docs/semester1`:

- `Project Proposal.docx`: the problem, proposed architecture, and technology choices.
- `Software Development Plan.docx`: requirements, scope, and planned deliverables.
- `dev_process.md`: the team's development workflow.

The development plan scopes the project to Purdue basketball players and an iOS-first app. The broader proposed system includes a FastAPI backend, PostgreSQL/PostGIS, Firebase authentication, and a server-side video analysis pipeline; those services are not required to run this frontend demo.

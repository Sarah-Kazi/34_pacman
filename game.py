import random
from collections import deque
import pygame

TILE = 28
MAZE = [
    "#####################",
    "#o........#........o#",
    "#.###.###.#.###.###.#",
    "#...................#",
    "#.###.#.#####.#.###.#",
    "#.....#...#...#.....#",
    "#####.###.#.###.#####",
    "#####.#.......#.#####",
    "#####.#.## ##.#.#####",
    "#.......#   #.......#",
    "#.###.#.#####.#.###.#",
    "#.###.#.......#.###.#",
    "#o..#...........#..o#",
    "#####################",
]
ROWS, COLS = len(MAZE), len(MAZE[0])
W, H = COLS * TILE, ROWS * TILE + 32
DIRS = [(-1, 0), (1, 0), (0, -1), (0, 1)]
KEY_DIRS = {pygame.K_UP: (-1, 0), pygame.K_DOWN: (1, 0), pygame.K_LEFT: (0, -1), pygame.K_RIGHT: (0, 1)}
HOUSE_CELLS = {(8, 10), (9, 9), (9, 10), (9, 11)}
HOUSE_EXIT, HOUSE_CENTER = (7, 10), (9, 10)
PLAYER_START = (11, 10)
FRIGHT_SECONDS = 3.0
PLAYER_STEP, GHOST_STEP = 0.14, 0.17
MAX_DT = 0.1


# Per-ghost frightened tints: all cool/dark so "scared" still reads at a glance,
# but each ghost stays identifiable.
FRIGHT_TINTS = {
    "blinky": (150, 40, 210),   # purple
    "pinky": (70, 110, 255),    # periwinkle
    "inky": (20, 150, 150),     # dark teal
    "clyde": (40, 160, 70),     # green
}
FRIGHT_FLASH = (240, 240, 240)
_fright_left = 0.0  # mirrored from Game.update so the hook can keep the expiry warning


def ghost_color(name, mode):
    """Return an (r, g, b) colour override for a ghost, or None to keep the default."""
    if mode != "frightened":
        return None  # normal keeps its colour; eaten draws eyes only, colour is unused
    if _fright_left < 1 and int(_fright_left * 6) % 2:
        return FRIGHT_FLASH  # same flash cadence as the default: fright is about to end
    return FRIGHT_TINTS.get(name)


# Bonus fruit: appears above the ghost house when pellets_left hits a trigger value.
# The hook has no Game reference, so effect state lives at module level as
# pygame.time.get_ticks() deadlines (0 = inactive).
FRUIT_CELL = (7, 10)
FRUIT_TRIGGERS = (90, 40)   # of 130 pellets: after 40 and 90 eaten
FRUIT_POINTS = 100
FRUIT_MS = 9000
WIN_FLASH_MS = 2000
_fruit_until = 0
_hud_flash_until = 0


def on_pellet_eaten(score, pellets_left):
    """Called after every pellet is eaten; add sound, flashes, or bonus fruit here."""
    global _fruit_until, _hud_flash_until
    now = pygame.time.get_ticks()
    if pellets_left in FRUIT_TRIGGERS:
        _fruit_until = now + FRUIT_MS
    if pellets_left == 0:
        _hud_flash_until = now + WIN_FLASH_MS


def fruit_active():
    return pygame.time.get_ticks() < _fruit_until


def clear_effects(hud=True):
    global _fruit_until, _hud_flash_until
    _fruit_until = 0
    if hud:
        _hud_flash_until = 0


BONUS_LIFE_EVERY = 1000  # a full clear is ~1450 from pellets alone, so 10000 would never fire


def bonus_life_threshold():
    """Return a score value at which the player earns an extra life, or None to disable bonus lives."""
    return BONUS_LIFE_EVERY


def valid_rgb(value):
    return (isinstance(value, (tuple, list)) and len(value) == 3
            and all(isinstance(v, int) and 0 <= v <= 255 for v in value))


def is_wall(cell):
    row, col = cell
    return not (0 <= row < ROWS and 0 <= col < COLS) or MAZE[row][col] == "#"


def _home_distances():
    """BFS distance from every open cell to HOUSE_CENTER. Greedy targeting with the
    no-reverse rule can trap eaten ghosts in loops (e.g. the corridor under the house),
    so eyes follow this map instead."""
    dist, queue = {HOUSE_CENTER: 0}, deque([HOUSE_CENTER])
    while queue:
        cur = queue.popleft()
        for d in DIRS:
            nxt = (cur[0] + d[0], cur[1] + d[1])
            if not is_wall(nxt) and nxt not in dist:
                dist[nxt] = dist[cur] + 1
                queue.append(nxt)
    return dist


HOME_DIST = _home_distances()


def target_for_pinky(player, direction):
    return (player[0] + direction[0] * 4, player[1] + direction[1] * 4)


def distance_sq(a, b):
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2


class Ghost:
    def __init__(self, name, color, start, corner, release):
        self.name, self.color, self.start, self.corner, self.release = name, color, start, corner, release
        self.reset()

    def reset(self):
        self.pos = self.start
        self.direction = (0, 0)
        self.eaten = False
        self.frightened = False
        self.skip = False
        self.timer = 0.0

    def target(self, player, direction, blinky, chase):
        if self.eaten:
            return HOUSE_CENTER
        if self.pos in HOUSE_CELLS:
            return HOUSE_EXIT
        if not chase:
            return self.corner
        if self.name == "pinky":
            return target_for_pinky(player, direction)
        if self.name == "inky":
            ahead = (player[0] + direction[0] * 2, player[1] + direction[1] * 2)
            return (2 * ahead[0] - blinky.pos[0], 2 * ahead[1] - blinky.pos[1])
        if self.name == "clyde":
            return self.corner if distance_sq(self.pos, player) < 64 else player
        return player

    def options(self):
        result = []
        for d in DIRS:
            cell = (self.pos[0] + d[0], self.pos[1] + d[1])
            if is_wall(cell):
                continue
            if cell in HOUSE_CELLS and self.pos not in HOUSE_CELLS and not self.eaten:
                continue
            result.append(d)
        back = (-self.direction[0], -self.direction[1])
        forward = [d for d in result if d != back]
        return forward or result

    def step(self, target, frightened):
        if self.eaten:
            self.step_home()
            return
        choices = self.options()
        if not choices:
            return
        if frightened:
            self.direction = random.choice(choices)
        else:
            self.direction = min(choices, key=lambda d: distance_sq((self.pos[0] + d[0], self.pos[1] + d[1]), target))
        self.pos = (self.pos[0] + self.direction[0], self.pos[1] + self.direction[1])

    def step_home(self):
        here = HOME_DIST.get(self.pos)
        for d in DIRS:
            nxt = (self.pos[0] + d[0], self.pos[1] + d[1])
            if here is not None and HOME_DIST.get(nxt) == here - 1:
                self.direction, self.pos = d, nxt
                break
        if self.pos == HOUSE_CENTER:
            self.eaten = False  # revived: frightened is already False, so it is dangerous again

    def reverse(self):
        self.direction = (-self.direction[0], -self.direction[1])


class Game:
    def __init__(self):
        self.ghosts = [
            Ghost("blinky", (255, 40, 40), (7, 9), (0, COLS - 1), 0.0),
            Ghost("pinky", (255, 150, 200), (9, 10), (0, 0), 2.0),
            Ghost("inky", (60, 220, 230), (9, 9), (ROWS - 1, COLS - 1), 5.0),
            Ghost("clyde", (255, 170, 40), (9, 11), (ROWS - 1, 0), 8.0),
        ]
        self.reset()

    def reset(self):
        self.pellets = {(r, c) for r, line in enumerate(MAZE) for c, v in enumerate(line) if v in ".o"}
        self.pellets.discard(PLAYER_START)
        self.player, self.direction, self.desired = list(PLAYER_START), (0, 1), (0, 1)
        self.score, self.lives, self.state = 0, 3, "play"
        self.bonus_awarded = 0
        self.clock_time = self.fright_left = self.player_acc = self.ghost_acc = 0.0
        clear_effects()
        for ghost in self.ghosts:
            ghost.reset()

    def respawn(self):
        clear_effects(hud=False)
        self.player, self.direction, self.desired = list(PLAYER_START), (0, 1), (0, 1)
        self.fright_left = 0.0
        for ghost in self.ghosts:
            ghost.reset()
        self.clock_time = self.player_acc = self.ghost_acc = 0.0

    def chasing(self):
        return (self.clock_time % 27) >= 7

    def move_player(self):
        for d in (self.desired, self.direction):
            cell = (self.player[0] + d[0], self.player[1] + d[1])
            if not is_wall(cell) and cell not in HOUSE_CELLS:
                self.direction = d
                self.player[:] = cell
                self.collect_fruit(tuple(cell))
                self.eat(tuple(cell))
                return

    def collect_fruit(self, cell):
        global _fruit_until
        if cell == FRUIT_CELL and fruit_active():
            _fruit_until = 0
            self.score += FRUIT_POINTS

    def eat(self, cell):
        if cell not in self.pellets:
            return
        self.pellets.remove(cell)
        self.score += 10
        if MAZE[cell[0]][cell[1]] == "o":
            self.score += 40
            self.fright_left = FRIGHT_SECONDS
            for ghost in self.ghosts:
                if not ghost.eaten:
                    ghost.frightened = True
                    ghost.reverse()
        on_pellet_eaten(self.score, len(self.pellets))
        if not self.pellets:
            self.state = "win"

    def check_collisions(self):
        if self.state != "play":
            return
        for ghost in self.ghosts:
            if ghost.pos != tuple(self.player) or ghost.eaten:
                continue
            if ghost.frightened:
                ghost.eaten = True
                ghost.frightened = False
                self.score += 200
            else:
                self.lives -= 1
                self.respawn()
                if self.lives <= 0:
                    self.state = "lose"
                return

    def award_bonus_lives(self):
        threshold = bonus_life_threshold()
        if not isinstance(threshold, int) or isinstance(threshold, bool) or threshold <= 0:
            return  # None or garbage from the hook disables bonus lives instead of crashing
        earned = self.score // threshold
        if earned > self.bonus_awarded:
            self.lives += earned - self.bonus_awarded  # one life per multiple crossed
            self.bonus_awarded = earned

    def update(self, dt):
        if self.state != "play":
            return
        dt = min(dt, MAX_DT)  # window drag / breakpoint stalls would otherwise teleport everything
        self.clock_time += dt
        self.fright_left = max(0.0, self.fright_left - dt)
        if self.fright_left == 0.0:
            for ghost in self.ghosts:
                ghost.frightened = False
        global _fright_left
        _fright_left = self.fright_left
        self.player_acc += dt
        while self.player_acc >= PLAYER_STEP and self.state == "play":
            self.player_acc -= PLAYER_STEP
            self.move_player()
            self.check_collisions()
        self.ghost_acc += dt
        while self.ghost_acc >= GHOST_STEP and self.state == "play":
            self.ghost_acc -= GHOST_STEP
            for ghost in self.ghosts:
                if self.clock_time < ghost.release:
                    continue
                scared = ghost.frightened
                if scared:
                    ghost.skip = not ghost.skip
                    if ghost.skip:
                        continue
                ghost.step(ghost.target(tuple(self.player), self.direction, self.ghosts[0], self.chasing()), scared)
            self.check_collisions()
        self.award_bonus_lives()

    def draw(self, screen, font):
        screen.fill((5, 5, 30))
        for r, line in enumerate(MAZE):
            for c, value in enumerate(line):
                rect = pygame.Rect(c * TILE, r * TILE, TILE, TILE)
                if value == "#":
                    pygame.draw.rect(screen, (20, 80, 180), rect.inflate(-4, -4), border_radius=6)
                elif (r, c) in self.pellets:
                    pygame.draw.circle(screen, (255, 220, 120), rect.center, 3 if value == "." else 7)
        if fruit_active():
            fx, fy = FRUIT_CELL[1] * TILE + TILE // 2, FRUIT_CELL[0] * TILE + TILE // 2
            pygame.draw.line(screen, (60, 200, 60), (fx - 5, fy + 2), (fx + 2, fy - 9), 2)
            pygame.draw.line(screen, (60, 200, 60), (fx + 5, fy + 3), (fx + 2, fy - 9), 2)
            pygame.draw.circle(screen, (230, 20, 40), (fx - 5, fy + 4), 6)
            pygame.draw.circle(screen, (230, 20, 40), (fx + 5, fy + 5), 6)
        px, py = self.player[1] * TILE + TILE // 2, self.player[0] * TILE + TILE // 2
        pygame.draw.circle(screen, (255, 220, 20), (px, py), TILE // 2 - 2)
        mouth = pygame.Vector2(self.direction[1], self.direction[0]) * (TILE // 2)
        if int(self.clock_time * 6) % 2 == 0 and mouth.length() > 0:
            side = pygame.Vector2(-mouth.y, mouth.x) * 0.6
            pygame.draw.polygon(screen, (5, 5, 30), [(px, py), (px + mouth.x + side.x, py + mouth.y + side.y),
                                                      (px + mouth.x - side.x, py + mouth.y - side.y)])
        for ghost in self.ghosts:
            gx, gy = ghost.pos[1] * TILE + TILE // 2, ghost.pos[0] * TILE + TILE // 2
            color = ghost.color
            if ghost.frightened:
                color = (240, 240, 240) if self.fright_left < 1 and int(self.fright_left * 6) % 2 else (40, 60, 230)
            mode = "eaten" if ghost.eaten else "frightened" if ghost.frightened else "normal"
            override = ghost_color(ghost.name, mode)
            if valid_rgb(override):
                color = override
            if ghost.eaten:
                pygame.draw.circle(screen, (240, 240, 240), (gx - 4, gy), 3)
                pygame.draw.circle(screen, (240, 240, 240), (gx + 4, gy), 3)
            else:
                pygame.draw.circle(screen, color, (gx, gy - 2), TILE // 2 - 3)
                pygame.draw.rect(screen, color, (gx - TILE // 2 + 3, gy - 2, TILE - 6, TILE // 2 - 2))
                pygame.draw.circle(screen, (255, 255, 255), (gx - 4, gy - 4), 3)
                pygame.draw.circle(screen, (255, 255, 255), (gx + 4, gy - 4), 3)
        now = pygame.time.get_ticks()
        hud_color = (255, 220, 20) if now < _hud_flash_until and (now // 150) % 2 else (240, 240, 240)
        hud = font.render(f"Score {self.score}   Lives {self.lives}   R = reset", True, hud_color)
        screen.blit(hud, (8, ROWS * TILE + 6))
        if self.state != "play":
            text = "YOU WIN! Press R" if self.state == "win" else "GAME OVER - Press R"
            label = font.render(text, True, (255, 255, 120))
            screen.blit(label, label.get_rect(center=(W // 2, H // 2)))


def main():
    pygame.init()
    screen = pygame.display.set_mode((W, H))
    pygame.display.set_caption("Pac-Man")
    clock = pygame.time.Clock()
    font = pygame.font.Font(None, 26)
    game = Game()
    running = True
    while running:
        dt = clock.tick(60) / 1000
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key in KEY_DIRS:
                    game.desired = KEY_DIRS[event.key]
                elif event.key == pygame.K_r:
                    game.reset()
        game.update(dt)
        game.draw(screen, font)
        pygame.display.flip()
    pygame.quit()


if __name__ == "__main__":
    main()

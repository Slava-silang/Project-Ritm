pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: pane
    required property var backend
    required property var appearance
    property bool active: visible
    readonly property var board: backend.gardenBoard
    readonly property var scene: backend.gardenScene
    readonly property bool narrow: width < appearance.dp(900)
    readonly property bool dialogsOpen: tools.visible || sale.visible || progress.visible
    readonly property bool writable: board.ready && backend.garden.enabled && !backend.busy &&
                                     !backend.gardenCommandPending
    property string selectedId: ""
    property string placingId: ""
    property string mode: "select"
    property string localError: ""
    property string sourceIdentity: ""
    property var hover: ({
                             valid: false
                         })
    property int zoom: 1
    property string tab: "shop"
    property int oldOriginX: 0
    property int oldOriginY: 0
    property bool hadScene: false
    readonly property var selected: (board.items || []).find(item => item.id === selectedId) || ({})
    readonly property var preview: {
        const revision = board.revision;
        return placingId && hover.valid ? backend.gardenPlacement(placingId, hover.x, hover.y) : ({
                                                                                                      error: "",
                                                                                                      cells: []
                                                                                                  });
    }
    signal leave

    function cancelPlacement() {
        placingId = "";
        mode = "select";
        localError = "";
    }
    function chooseItem(identifier) {
        selectedId = identifier;
        localError = "";
        if (narrow)
            tools.open();
    }
    function beginPlacement() {
        placingId = selectedId;
        mode = "select";
        localError = "";
        tools.close();
        viewport.forceActiveFocus();
    }
    function clickCell(px, py) {
        hover = backend.gardenCellAt(px, py);
        if (!hover.valid || !writable)
            return;
        localError = "";
        if (placingId) {
            if (preview.error) {
                localError = preview.error;
                return;
            }
            backend.gardenCommand({
                                      action: "place",
                                      id: placingId,
                                      x: hover.x,
                                      y: hover.y
                                  });
        } else if (mode !== "select") {
            if (hover.itemId) {
                localError = qsTr("Грунт меняется только на свободной клетке.");
                return;
            }
            backend.gardenCommand({
                                      action: "terrain",
                                      x: hover.x,
                                      y: hover.y,
                                      terrain: mode
                                  });
        } else {
            selectedId = hover.itemId;
            if (selectedId && narrow)
                tools.open();
        }
        viewport.forceActiveFocus();
    }
    function clampView() {
        viewport.contentX = Math.max(0, Math.min(viewport.contentX, viewport.contentWidth
                                                 - viewport.width));
        viewport.contentY = Math.max(0, Math.min(viewport.contentY, viewport.contentHeight
                                                 - viewport.height));
    }
    function centerView() {
        viewport.contentX = Math.max(0, (viewport.contentWidth - viewport.width) / 2);
        viewport.contentY = Math.max(0, (viewport.contentHeight - viewport.height) / 2);
    }
    function changeZoom(value) {
        const cx = (viewport.contentX + viewport.width / 2) / zoom;
        const cy = (viewport.contentY + viewport.height / 2) / zoom;
        zoom = value;
        Qt.callLater(function () {
            viewport.contentX = cx * zoom - viewport.width / 2;
            viewport.contentY = cy * zoom - viewport.height / 2;
            clampView();
        });
    }
    function keyboardCell(dx, dy) {
        const x = Math.max(0, Math.min(9, (hover.valid ? hover.x : 5) + dx));
        const y = Math.max(0, Math.min(9, (hover.valid ? hover.y : 5) + dy));
        const cell = (scene.cells || []).find(c => c.x === x && c.y === y);
        if (!cell)
            return;
        hover = backend.gardenCellAt(cell.centerX, cell.centerY);
        viewport.contentX = cell.centerX * zoom - viewport.width / 2;
        viewport.contentY = cell.centerY * zoom - viewport.height / 2;
        clampView();
    }
    onVisibleChanged: if (!visible) {
                          tools.close();
                          sale.close();
                          progress.close();
                          cancelPlacement();
                      }
    onPreviewChanged: if (worldLoader.item)
                          worldLoader.item.repaint()
    onHoverChanged: if (worldLoader.item)
                        worldLoader.item.repaint()
    onSelectedChanged: if (worldLoader.item)
                           worldLoader.item.repaint()
    Connections {
        target: pane.backend
        function onGardenCommandDone(identifier: string) {
            pane.placingId = "";
            pane.localError = "";
            if (identifier && pane.board.items.some(item => item.id === identifier)) {
                pane.selectedId = identifier;
                pane.tab = "inventory";
            }
        }
        function onGardenBoardChanged() {
            if (pane.board.identity !== pane.sourceIdentity) {
                pane.selectedId = "";
                pane.cancelPlacement();
                sale.close();
                pane.sourceIdentity = pane.board.identity;
            }
            if (!pane.board.items.some(item => item.id === pane.selectedId))
                pane.selectedId = "";
            if (!pane.board.ready) {
                pane.cancelPlacement();
                tools.close();
                sale.close();
            }
        }
        function onGardenSceneChanged() {
            const dx = ((pane.scene.originX || 0) - pane.oldOriginX) * pane.zoom;
            const dy = ((pane.scene.originY || 0) - pane.oldOriginY) * pane.zoom;
            const first = !pane.hadScene;
            pane.hadScene = !!pane.scene.layers;
            pane.oldOriginX = pane.scene.originX || 0;
            pane.oldOriginY = pane.scene.originY || 0;
            Qt.callLater(function () {
                if (first)
                    pane.centerView();
                else {
                    viewport.contentX += dx;
                    viewport.contentY += dy;
                    pane.clampView();
                }
            });
        }
    }
    Shortcut {
        sequence: "Escape"
        enabled: pane.active && !pane.dialogsOpen && (!!pane.placingId || pane.mode !== "select")
        onActivated: pane.cancelPlacement()
    }
    ColumnLayout {
        anchors.fill: parent
        spacing: pane.appearance.dp(8)
        Flow {
            Layout.fillWidth: true
            spacing: pane.appearance.dp(6)
            CheckBox {
                objectName: "gardenEnabled"
                text: pane.narrow ? qsTr("Учёт сада") : qsTr("Учитывать работу для сада")
                checked: pane.backend.garden.enabled
                enabled: !pane.backend.busy && !pane.backend.gardenCommandPending
                onClicked: pane.backend.setGardenEnabled(checked)
            }
            Label {
                objectName: "gardenBalance"
                text: pane.backend.garden.error ? qsTr("Монеты: —") : qsTr("Монеты: %1").arg(
                                                      pane.backend.garden.balance)
                padding: pane.appearance.dp(8)
            }
            Label {
                visible: pane.board.ready && !pane.narrow
                text: qsTr("Удобрение: %1").arg(pane.board.fertilizer)
                padding: pane.appearance.dp(8)
            }
            Button {
                objectName: "gardenProgressWhenOff"
                visible: pane.backend.garden.subscribed && (!pane.backend.garden.enabled ||
                                                            !pane.board.ready || pane.narrow)
                text: qsTr("Прогресс")
                onClicked: progress.open()
            }
            Button {
                objectName: "closeGarden"
                text: qsTr("К задачам")
                onClicked: pane.leave()
            }
        }
        Label {
            Layout.fillWidth: true
            visible: pane.board.ready
            text: pane.narrow ? qsTr("Взрослых: %1 / 80 · Занято: %2 / 80").arg(
                                    pane.board.matureCells).arg(pane.board.plantedCells) : qsTr(
                                    "%1 · Взрослых клеток: %2 / 80 · Растениями занято: %3 / 80").arg(
                                    pane.board.month).arg(pane.board.matureCells).arg(
                                    pane.board.plantedCells)
            wrapMode: Text.Wrap
        }
        Label {
            objectName: "gardenError"
            Layout.fillWidth: true
            visible: !!text
            text: pane.backend.garden.error || pane.board.error || pane.localError || (
                      pane.placingId ? pane.preview.error : "")
            color: pane.appearance.colors.warning
            wrapMode: Text.Wrap
        }
        Flow {
            Layout.fillWidth: true
            visible: !!pane.backend.garden.error || !!pane.board.error
                     || pane.backend.gardenCommandPending
            spacing: pane.appearance.dp(6)
            Button {
                objectName: "retryGarden"
                text: qsTr("Обновить сад")
                enabled: !pane.backend.busy
                onClicked: pane.backend.reload()
            }
            Button {
                objectName: "retryGardenCommand"
                visible: pane.backend.gardenCommandPending
                text: qsTr("Проверить действие")
                enabled: !pane.backend.busy
                onClicked: pane.backend.retryGardenCommand()
            }
        }
        Label {
            Layout.fillWidth: true
            visible: !pane.backend.garden.enabled
            text: qsTr(
                      "Учёт выключен. Включите его, чтобы открыть участок. Монеты и достижения сохраняются; выключенный промежуток не начисляется.")
            wrapMode: Text.Wrap
        }
        Flow {
            Layout.fillWidth: true
            visible: pane.board.ready
            spacing: pane.appearance.dp(4)
            ComboBox {
                objectName: "gardenMode"
                width: pane.appearance.dp(pane.narrow ? 130 : 170)
                model: [qsTr("Выбор"), qsTr("Трава"), qsTr("Грядка"), qsTr("Вода")]
                currentIndex: ["select", "grass", "tilled_soil", "water"].indexOf(pane.mode)
                enabled: pane.writable
                onActivated: index => {
                    pane.cancelPlacement();
                    pane.mode = ["select", "grass", "tilled_soil", "water"][index];
                }
            }
            Button {
                objectName: "gardenZoom1"
                visible: !pane.narrow
                text: "1×"
                highlighted: pane.zoom === 1
                onClicked: pane.changeZoom(1)
            }
            Button {
                objectName: "gardenZoom2"
                text: pane.narrow && pane.zoom === 2 ? "1×" : "2×"
                highlighted: pane.zoom === 2
                onClicked: pane.changeZoom(pane.narrow && pane.zoom === 2 ? 1 : 2)
            }
            Button {
                objectName: "gardenCenter"
                visible: !pane.narrow
                text: qsTr("Центр")
                onClicked: pane.centerView()
            }
            Button {
                objectName: "gardenTools"
                visible: pane.narrow
                text: qsTr("Предметы")
                onClicked: tools.open()
            }
            Button {
                objectName: "gardenCancelPlacement"
                visible: !!pane.placingId || pane.mode !== "select"
                text: qsTr("Отмена · Esc")
                onClicked: pane.cancelPlacement()
            }
        }
        Label {
            Layout.fillWidth: true
            visible: !!pane.placingId
            text: pane.narrow ? qsTr("Посадка: %1 · Esc — отмена").arg(pane.selected.title || "") : qsTr(
                                    "Выберите свободное основание для «%1». Предмет останется в инвентаре до размещения.").arg(
                                    pane.selected.title || "")
            wrapMode: Text.Wrap
        }
        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: pane.appearance.dp(10)
            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                color: pane.appearance.colors.surface
                border.color: pane.appearance.colors.border
                radius: pane.appearance.dp(10)
                clip: true
                Flickable {
                    id: viewport
                    objectName: "gardenViewport"
                    property real previousWidth: 0
                    property real previousHeight: 0
                    onWidthChanged: {
                        if (previousWidth && pane.scene.layers)
                            contentX += (previousWidth - width) / 2;
                        previousWidth = width;
                        Qt.callLater(pane.clampView);
                    }
                    onHeightChanged: {
                        if (previousHeight && pane.scene.layers)
                            contentY += (previousHeight - height) / 2;
                        previousHeight = height;
                        Qt.callLater(pane.clampView);
                    }
                    anchors.fill: parent
                    clip: true
                    activeFocusOnTab: true
                    contentWidth: (pane.scene.width || 0) * pane.zoom
                    contentHeight: (pane.scene.height || 0) * pane.zoom
                    boundsBehavior: Flickable.StopAtBounds
                    ScrollBar.horizontal: ScrollBar {}
                    ScrollBar.vertical: ScrollBar {}
                    Keys.onPressed: event => {
                        if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
                            if (!pane.hover.valid)
                                pane.keyboardCell(0, 0);
                            const cell = (pane.scene.cells || []).find(c => c.x === pane.hover.x
                                                                            && c.y
                                                                            === pane.hover.y);
                            if (cell)
                                pane.clickCell(cell.centerX, cell.centerY);
                            event.accepted = true;
                            return;
                        }
                        if (event.modifiers & Qt.ShiftModifier) {
                            if (event.key === Qt.Key_Left)
                                pane.keyboardCell(-1, 0);
                            else if (event.key === Qt.Key_Right)
                                pane.keyboardCell(1, 0);
                            else if (event.key === Qt.Key_Up)
                                pane.keyboardCell(0, -1);
                            else if (event.key === Qt.Key_Down)
                                pane.keyboardCell(0, 1);
                            else
                                return;
                            event.accepted = true;
                            return;
                        }
                        if (event.key === Qt.Key_Left)
                            contentX -= 32;
                        else if (event.key === Qt.Key_Right)
                            contentX += 32;
                        else if (event.key === Qt.Key_Up)
                            contentY -= 32;
                        else if (event.key === Qt.Key_Down)
                            contentY += 32;
                        else
                            return;
                        pane.clampView();
                        event.accepted = true;
                    }
                    Accessible.name: qsTr(
                                         "Поле сада. Стрелки — прокрутка, Shift и стрелки — выбор клетки, Enter — действие, Escape — отмена.")
                    Loader {
                        id: worldLoader
                        objectName: "gardenWorld"
                        x: Math.max(0, (viewport.width - (pane.scene.width || 0) * pane.zoom) / 2)
                        y: Math.max(0, (viewport.height - (pane.scene.height || 0) * pane.zoom) / 2)
                        active: pane.active && pane.backend.garden.enabled && pane.board.ready
                        sourceComponent: Item {
                            id: world
                            width: pane.scene.width || 0
                            height: pane.scene.height || 0
                            scale: pane.zoom
                            transformOrigin: Item.TopLeft
                            function repaint() {
                                highlight.requestPaint();
                            }
                            Repeater {
                                model: pane.scene.layers || []
                                delegate: Image {
                                    required property var modelData
                                    required property int index
                                    objectName: "gardenSprite_" + index
                                    x: modelData.x
                                    y: modelData.y
                                    width: modelData.width
                                    height: modelData.height
                                    source: modelData.source
                                    smooth: false
                                    mipmap: false
                                    asynchronous: true
                                }
                            }
                            Canvas {
                                id: highlight
                                anchors.fill: parent
                                onPaint: {
                                    const ctx = getContext("2d");
                                    ctx.clearRect(0, 0, width, height);
                                    let cells = [];
                                    if (pane.placingId)
                                        cells = pane.preview.cells;
                                    else if (pane.selected.id && pane.selected.placed)
                                        cells = pane.selected.offsets.map(p => ({
                                            x: pane.selected.x + p[0],
                                            y: pane.selected.y + p[1]
                                        }));
                                    else if (pane.hover.valid)
                                        cells = [pane.hover];
                                    ctx.strokeStyle = pane.preview.error
                                            ? pane.appearance.colors.warning :
                                              pane.appearance.colors.accent;
                                    ctx.fillStyle = pane.preview.error ? "#55cc5555" : "#4477bb77";
                                    ctx.lineWidth = 2;
                                    for (const cell of cells) {
                                        const cx = pane.scene.originX + (cell.x - cell.y) * 32;
                                        const cy = pane.scene.originY + (cell.x + cell.y) * 16;
                                        ctx.beginPath();
                                        ctx.moveTo(cx, cy - 16);
                                        ctx.lineTo(cx + 32, cy);
                                        ctx.lineTo(cx, cy + 16);
                                        ctx.lineTo(cx - 32, cy);
                                        ctx.closePath();
                                        ctx.fill();
                                        ctx.stroke();
                                    }
                                }
                            }
                            MouseArea {
                                objectName: "gardenFieldMouse"
                                anchors.fill: parent
                                enabled: pane.writable
                                hoverEnabled: true
                                acceptedButtons: Qt.LeftButton | Qt.RightButton
                                onPositionChanged: mouse => {
                                    pane.hover = pane.backend.gardenCellAt(mouse.x, mouse.y);
                                }
                                onClicked: mouse => {
                                    if (mouse.button === Qt.RightButton)
                                        pane.cancelPlacement();
                                    else
                                        pane.clickCell(mouse.x, mouse.y);
                                }
                            }
                        }
                    }
                }
            }
            Loader {
                Layout.preferredWidth: pane.appearance.dp(290)
                Layout.fillHeight: true
                visible: !pane.narrow && pane.board.ready
                active: visible && pane.active
                sourceComponent: panel
            }
        }
    }
    Drawer {
        id: tools
        objectName: "gardenDrawer"
        parent: Overlay.overlay
        edge: Qt.RightEdge
        width: Math.min(pane.appearance.dp(340), parent.width - 24)
        height: parent.height
        modal: true
        contentItem: Loader {
            active: tools.visible
            sourceComponent: panel
        }
    }
    Component {
        id: panel
        ColumnLayout {
            spacing: pane.appearance.dp(6)
            RowLayout {
                Layout.fillWidth: true
                Label {
                    Layout.fillWidth: true
                    text: qsTr("Монеты: %1 · Удобрение: %2").arg(pane.backend.garden.balance).arg(
                              pane.board.fertilizer)
                    wrapMode: Text.Wrap
                }
                ToolButton {
                    objectName: "gardenProgressButton"
                    text: qsTr("Прогресс")
                    onClicked: progress.open()
                }
            }
            RowLayout {
                Button {
                    objectName: "gardenShopTab"
                    text: qsTr("Магазин")
                    highlighted: pane.tab === "shop"
                    onClicked: pane.tab = "shop"
                }
                Button {
                    objectName: "gardenInventoryTab"
                    text: qsTr("Инвентарь")
                    highlighted: pane.tab === "inventory"
                    onClicked: pane.tab = "inventory"
                }
                Button {
                    visible: pane.narrow
                    text: "×"
                    onClicked: tools.close()
                    Accessible.name: qsTr("Закрыть панель")
                }
            }
            ScrollView {
                id: inspector
                Layout.fillWidth: true
                Layout.preferredHeight: Math.min(pane.appearance.dp(220), pane.height / 2)
                visible: !!pane.selected.id
                contentWidth: availableWidth
                clip: true
                ColumnLayout {
                    width: inspector.availableWidth
                    Label {
                        Layout.fillWidth: true
                        text: pane.selected.title || ""
                        font.bold: true
                        wrapMode: Text.Wrap
                    }
                    Label {
                        text: pane.selected.growing ? qsTr("Стадия %1 / 4").arg(
                                                          pane.selected.stage) : qsTr("Украшение")
                    }
                    Flow {
                        Layout.fillWidth: true
                        spacing: pane.appearance.dp(4)
                        Button {
                            objectName: "gardenMove"
                            text: pane.selected.placed ? qsTr("Переместить") : qsTr("Разместить")
                            enabled: pane.writable
                            onClicked: pane.beginPlacement()
                        }
                        Button {
                            objectName: "gardenStash"
                            text: qsTr("В инвентарь")
                            visible: !!pane.selected.placed
                            enabled: pane.writable
                            onClicked: pane.backend.gardenCommand({
                                                                      action: "stash",
                                                                      id: pane.selectedId
                                                                  })
                        }
                        Button {
                            objectName: "gardenGrow"
                            text: qsTr("Удобрить · %1 ед.").arg(pane.selected.dose || 0)
                            visible: !!pane.selected.growing && pane.selected.stage < 4
                            enabled: pane.writable && !!pane.selected.canGrow
                            onClicked: pane.backend.gardenCommand({
                                                                      action: "grow",
                                                                      id: pane.selectedId
                                                                  })
                        }
                        Button {
                            objectName: "gardenSell"
                            text: qsTr("Продать · %1").arg(pane.selected.refund || 0)
                            visible: !!pane.selected.growing
                            enabled: pane.writable
                            onClicked: {
                                sale.itemId = pane.selectedId;
                                sale.itemTitle = pane.selected.title;
                                sale.refund = pane.selected.refund;
                                sale.open();
                            }
                        }
                    }
                    Label {
                        Layout.fillWidth: true
                        visible: !!pane.selected.growing && !!pane.selected.growReason
                        text: pane.selected.growReason || ""
                        wrapMode: Text.Wrap
                    }
                }
            }
            ScrollView {
                id: listScroll
                Layout.fillWidth: true
                Layout.fillHeight: true
                contentWidth: availableWidth
                clip: true
                ColumnLayout {
                    width: listScroll.availableWidth
                    spacing: pane.appearance.dp(8)
                    Repeater {
                        model: pane.tab === "shop" ? pane.board.catalog : pane.board.items
                        delegate: Rectangle {
                            id: card
                            required property var modelData
                            Layout.fillWidth: true
                            implicitHeight: cardContent.implicitHeight + pane.appearance.dp(12)
                            color: pane.appearance.colors.raised
                            radius: pane.appearance.dp(8)
                            RowLayout {
                                id: cardContent
                                anchors.fill: parent
                                anchors.margins: pane.appearance.dp(6)
                                Image {
                                    Layout.preferredWidth: pane.appearance.dp(54)
                                    Layout.preferredHeight: pane.appearance.dp(62)
                                    source: card.modelData.source
                                    fillMode: Image.PreserveAspectFit
                                    smooth: false
                                }
                                ColumnLayout {
                                    Layout.fillWidth: true
                                    Label {
                                        Layout.fillWidth: true
                                        text: card.modelData.title
                                        wrapMode: Text.Wrap
                                    }
                                    Label {
                                        Layout.fillWidth: true
                                        text: pane.tab === "shop" ? qsTr("%1 монет · %2 кл.").arg(
                                                                        card.modelData.price).arg(
                                                                        card.modelData.cells) :
                                                                    card.modelData.growing ? qsTr(
                                                                                                 "Стадия %1/4 · %2").arg(
                                                                                                 card.modelData.stage).arg(
                                                                                                 card.modelData.placed
                                                                                                 ? qsTr("На поле") :
                                                                                                   qsTr("В запасе")) :
                                                                                             card.modelData.placed
                                                                                             ? qsTr("На поле") :
                                                                                               qsTr("В запасе")
                                        wrapMode: Text.Wrap
                                    }
                                    Label {
                                        Layout.fillWidth: true
                                        visible: pane.tab === "shop"
                                        text: pane.tab === "shop" ? (card.modelData.growing ? qsTr(
                                                                                                  "Побег 1/4 · ") :
                                                                                              "") + card.modelData.terrain :
                                                                    ""
                                        wrapMode: Text.Wrap
                                        font.pixelSize: pane.appearance.dp(12)
                                    }
                                    Button {
                                        objectName: pane.tab === "shop" ? "gardenBuy_"
                                                                          + card.modelData.kind :
                                                                          "gardenChoose_"
                                                                          + card.modelData.id
                                        text: pane.tab === "shop" ? (card.modelData.canBuy ? qsTr(
                                                                                                 "Купить") :
                                                                                             qsTr("Не хватает монет")) :
                                                                    qsTr("Выбрать")
                                        enabled: pane.writable && (pane.tab !== "shop" || !
                                                                   !card.modelData.canBuy)
                                        onClicked: {
                                            if (pane.tab === "shop")
                                                pane.backend.gardenCommand({
                                                                               action: "buy",
                                                                               kind: card.modelData.kind
                                                                           });
                                            else
                                                pane.chooseItem(card.modelData.id);
                                        }
                                    }
                                }
                            }
                        }
                    }
                    Label {
                        Layout.fillWidth: true
                        visible: pane.tab === "inventory" && !pane.board.items.length
                        text: qsTr("Предметов пока нет. Купите побег в магазине.")
                        wrapMode: Text.Wrap
                    }
                    ColumnLayout {
                        Layout.fillWidth: true
                        visible: pane.tab === "shop"
                        Label {
                            text: qsTr("Удобрение · 10 монет за единицу")
                        }
                        TextField {
                            id: fertilizer
                            objectName: "gardenFertilizerQuantity"
                            Layout.fillWidth: true
                            text: "1"
                            placeholderText: qsTr("Количество")
                            validator: RegularExpressionValidator {
                                regularExpression: /[0-9]{1,12}/
                            }
                        }
                        Label {
                            readonly property var quote: {
                                const revision = pane.board.revision;
                                return pane.backend.gardenQuote(fertilizer.text);
                            }
                            text: qsTr("Всего: %1 монет").arg(quote.cost)
                        }
                        Button {
                            objectName: "gardenBuyFertilizer"
                            readonly property var quote: {
                                const revision = pane.board.revision;
                                return pane.backend.gardenQuote(fertilizer.text);
                            }
                            text: qsTr("Купить удобрение")
                            enabled: pane.writable && quote.canBuy
                            onClicked: pane.backend.gardenCommand({
                                                                      action: "buy_fertilizer",
                                                                      quantity: quote.quantity
                                                                  })
                        }
                    }
                }
            }
        }
    }
    GardenProgress {
        id: progress
        backend: pane.backend
        appearance: pane.appearance
    }
    Dialog {
        id: sale
        property string itemId: ""
        property string itemTitle: ""
        property int refund: 0
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(pane.appearance.dp(440), parent.width - 32)
        height: Math.min(pane.appearance.dp(280), parent.height - 32)
        title: qsTr("Продать растение?")
        modal: true
        contentItem: Label {
            text: qsTr(
                      "«%1»: вернётся %2 монет — половина цены растения. Потраченное удобрение не возвращается.").arg(
                      sale.itemTitle).arg(sale.refund)
            textFormat: Text.PlainText
            wrapMode: Text.Wrap
        }
        footer: RowLayout {
            Button {
                objectName: "gardenCancelSale"
                text: qsTr("Отмена")
                enabled: !pane.backend.busy
                onClicked: sale.close()
            }
            Button {
                objectName: "gardenConfirmSale"
                text: qsTr("Продать")
                enabled: pane.writable
                onClicked: {
                    pane.backend.gardenCommand({
                                                   action: "sell",
                                                   id: sale.itemId
                                               });
                    sale.close();
                }
            }
        }
    }
}

pragma ComponentBehavior: Bound

import QtQuick 2.15
import QtQuick.Controls 2.15

Item {
    id: root

    required property var pool
    required property bool narrow
    signal previewRequested(int grpId)
    signal previewDismissed()

    readonly property var recentPicks: root.pool && root.pool.recent_picks
        ? root.pool.recent_picks : []
    readonly property int targetColumns: root.narrow ? 4 : 6
    readonly property int gap: 6
    readonly property var previewedGrpId: root.pool
        ? root.pool.previewed_recent_pick_grp_id : null
    readonly property var previewedPick: {
        const picks = root.recentPicks
        const grpId = root.previewedGrpId
        if (grpId === null || grpId === undefined)
            return null
        for (let index = 0; index < picks.length; index++) {
            const pick = picks[index]
            if (pick && pick.card && pick.card.grp_id === grpId)
                return pick
        }
        return null
    }

    property var returnFocusItem: null

    implicitHeight: galleryColumn.implicitHeight
    Accessible.role: Accessible.Pane
    Accessible.name: "Recent picks"
    Accessible.description: "Cards picked most recently, newest first."

    function handleActivation(grpId, opener) {
        root.returnFocusItem = opener
        root.previewRequested(grpId)
    }

    function syncPreviewDialog() {
        if (!root.previewedPick) {
            if (previewDialog.opened)
                previewDialog.close()
            return
        }
        if (!previewDialog.opened)
            previewDialog.open()
    }

    onPreviewedGrpIdChanged: Qt.callLater(root.syncPreviewDialog)
    onRecentPicksChanged: Qt.callLater(root.syncPreviewDialog)

    Column {
        id: galleryColumn
        width: root.width
        spacing: 8

        Label {
            id: heading
            width: parent.width
            text: "RECENT PICKS"
            color: Theme.textMuted
            font.pixelSize: 11
            font.bold: true
            font.letterSpacing: 1
        }

        Grid {
            id: recentPicksGrid
            objectName: "recentPicksGrid"
            width: parent.width
            columns: Math.min(root.targetColumns, Math.max(1,
                Math.floor((width + root.gap) / (64 + root.gap))))
            columnSpacing: root.gap
            rowSpacing: root.gap
            height: childrenRect.height

            Repeater {
                model: root.recentPicks

                delegate: RecentPickThumbnail {
                    required property int index
                    required property var modelData

                    pickIndex: index
                    recentPick: modelData
                    width: Math.max(1, (recentPicksGrid.width
                        - (recentPicksGrid.columns - 1) * root.gap)
                        / recentPicksGrid.columns)
                    onActivated: grpId => root.handleActivation(grpId, this)
                }
            }
        }
    }

    Dialog {
        id: previewDialog
        objectName: "recentPickPreviewDialog"
        property var returnFocusItem: null
        parent: Overlay.overlay
        modal: true
        focus: true
        title: root.previewedPick && root.previewedPick.card
            ? root.previewedPick.card.name : "Card preview"
        implicitWidth: Math.min(420, Math.max(280, root.width - 32))

        onRejected: root.previewDismissed()
        onClosed: {
            const opener = root.returnFocusItem
            previewDialog.returnFocusItem = null
            root.returnFocusItem = null
            if (opener && opener.visible && opener.enabled)
                opener.forceActiveFocus()
        }

        contentItem: Item {
            id: previewContent
            implicitWidth: 320
            implicitHeight: 448

            Image {
                id: previewImage
                objectName: "recentPickPreviewImage"
                anchors.fill: parent
                anchors.margins: 12
                source: root.previewedPick && root.previewedPick.image
                    && root.previewedPick.image.phase === "ready"
                    && root.previewedPick.image.image_path
                    ? root.previewedPick.image.image_path : ""
                fillMode: Image.PreserveAspectFit
                asynchronous: true
                visible: status === Image.Ready
            }

            Label {
                id: previewFallbackLabel
                objectName: "recentPickPreviewFallbackLabel"
                anchors.fill: parent
                anchors.margins: 28
                text: {
                    const image = root.previewedPick
                        ? root.previewedPick.image : null
                    if (previewImage.status === Image.Error)
                        return "Image failed to display"
                    if (image && image.phase === "loading")
                        return "Loading image"
                    if (image && image.phase === "failed")
                        return "Image unavailable"
                    if (image && image.phase === "ready"
                            && previewImage.source.toString().length > 0)
                        return "Loading image"
                    return "No image available"
                }
                color: Theme.textMuted
                font.pixelSize: 15
                font.bold: true
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
                wrapMode: Text.Wrap
                visible: !previewImage.visible
                Accessible.name: text
            }
        }

        footer: DialogButtonBox {
            Button {
                objectName: "recentPickPreviewCloseButton"
                text: "Close"
                Accessible.name: "Close"
                onClicked: previewDialog.reject()
            }
        }
    }
}
